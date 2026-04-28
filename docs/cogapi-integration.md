# Flowise ↔ cogapi: request flow & authentication

How a user's "open the ChatOpenAI Custom Model Name dropdown" click in
the Flowise UI ends up populating from cogapi-served LLMs. Useful when
debugging an empty dropdown or "permissions look right but data is empty"
classes of failure.

## End-to-end request chain

```
Browser ──> Istio ingress ──> Flowise pod ──> cog-api(-dev) pod ──> KServe ISVCs
            (kubeflow-userid)   (forwards header)   (reads annotations)
```

1. **Browser → Istio.** The Kubeflow ingress (Istio + Dex/oidc-gatekeeper)
   authenticates the user and injects the `kubeflow-userid` HTTP header on
   every forwarded request.

2. **Istio → Flowise.** Two things happen on the Flowise side:
   - On first hit, Flowise's trusted-header path
     (`packages/server/src/enterprise/middleware/passport/index.ts`,
     `app.post('${basePath}/api/v1/auth/resolve')`) reads
     `req.headers[TRUSTED_AUTH_HEADER.toLowerCase()]` (the charm sets
     `TRUSTED_AUTH_HEADER=kubeflow-userid` by default), auto-provisions
     the user if needed, and sets the `flowise.sid` session cookie.
   - Subsequent UI requests carry both the cookie AND `kubeflow-userid`
     (Istio re-injects it on every request).

3. **Flowise UI → Flowise server (loadMethod).** When the dropdown opens,
   `AsyncDropdown.jsx` POSTs to
   `${basePath}/api/v1/node-load-method/<nodeName>` with three things:
   - **Session cookie** (`flowise.sid`) — auth.
   - **`x-request-from: internal`** header — required by Flowise's API
     middleware to allow internal API traffic without a separate API key.
   - **`kubeflow-userid`** header — re-injected by Istio.

   Missing the cookie → `401 Unauthorized`.
   Missing `x-request-from: internal` → `401 Unauthorized`.

4. **Flowise controller → service → loadMethod.**
   `packages/server/src/controllers/nodes/index.ts:getSingleNodeAsyncOptions`
   reads `req.headers[TRUSTED_AUTH_HEADER]` and stashes it on the request
   body as `trustedUserIdentity`. The service forwards it into the
   `loadMethods` options bag. The node's `listModels` reads
   `options.trustedUserIdentity` and forwards it as `kubeflow-userid` on
   the cogapi `fetch`. The component-level cache key is
   `(cogapiUrl, userIdentity)` so different users don't share lists.

5. **Flowise → cogapi.** In-cluster, pod-to-pod over Kubernetes service
   DNS (`http://cog-api{base-path}/models-serving`, e.g.
   `http://cog-api-dev/apidev/models-serving`). The charm builds
   `COG_API_URL` from the `cog-api-info` relation
   (`<remote-app-name>/<base-path>` with normalisation). The fetch carries
   the forwarded `kubeflow-userid` header.

6. **cogapi → KServe.** cogapi reads `kubeflow-userid` to determine the
   user's Kubeflow namespace, lists InferenceServices in that namespace,
   and projects each one through `cogflow/core/serving._process_isvc`.

## Auth quirks worth remembering

- **Istio injects `kubeflow-userid` on EVERY forwarded request**, not
  just on auth/resolve. That's what makes per-user load-method scoping
  feasible without re-checking the session.
- **Pod-to-pod calls inside the cluster bypass Istio.** Flowise calling
  cog-api directly via service DNS does NOT have `kubeflow-userid`
  unless the caller forwards it explicitly. Without it, cogapi returns
  `data: []` (empty list — NOT 401). That's a silent failure.
- **`x-request-from: internal` is part of Flowise's auth gate**, not
  just metadata. Curl probes from inside the pod must include both the
  session cookie AND this header to get past the API middleware.

## cogapi `/models-serving` per-user scoping

cogapi (cog-engine `app/api/models.py:get_model_serving`) filters
results by `kubeflow-userid`:

- The header maps to a Kubeflow Profile → namespace.
- ISVCs in that namespace are listed and annotated.
- No header / unknown user → empty `data: []`.

## ISVC annotation shape

`cogflow/core/serving._process_isvc` extracts metadata from the ISVC's
`metadata.annotations`:

| Field returned to the client | K8s annotation |
|---|---|
| `model_name` | `model_name` |
| `model_id` | `model_id` |
| `model_version` | `model_version` |
| `dataset_id` | `dataset_id` |
| `model_type` | `model_type` (for LLMs: `"llm"`) |

Missing annotations → corresponding fields come back as `null`. Two
common cases that affect the dropdown:

- **`model_name` annotation missing** → `null` in the response. The
  ChatOpenAI Custom node falls back to `isvc_name` (cog-flow PR #18).
- **`model_type` annotation missing** → entry is filtered out client-
  side (we only show `model_type === "llm"`). Operators must annotate
  LLMs with `model_type: llm`.

To set the annotation post-deploy:

```sh
kubectl annotate isvc -n <namespace> <isvc-name> model_name=<name> --overwrite
```

## Debugging an empty dropdown

Walk the chain from outside in. Each step has a definitive yes/no answer.

```sh
# 1. Pod is on the right image (the one with PR #15 + #18 in)?
kubectl exec -n kubeflow flowise-0 -c flowise -- sh -c '
  grep -c trustedUserIdentity /usr/src/flowise/packages/server/dist/controllers/nodes/index.js
  grep -c kubeflow-userid /usr/src/flowise/packages/components/dist/nodes/chatmodels/ChatOpenAICustom/ChatOpenAICustom.js
'
# Both should be > 0.

# 2. COG_API_URL set in the workload env?
kubectl exec -n kubeflow flowise-0 -c flowise -- sh -c '
  PID=$(pgrep -f "flowise start" | head -1)
  cat /proc/$PID/environ | tr "\0" "\n" | grep COG_API_URL
'

# 3. cogapi returns data for the calling user?
kubectl exec -n kubeflow flowise-0 -c flowise -- sh -c '
  PID=$(pgrep -f "flowise start" | head -1)
  URL=$(cat /proc/$PID/environ | tr "\0" "\n" | awk -F= "/^COG_API_URL=/{print \$2}")
  curl -sS -H "kubeflow-userid: <user>" "$URL/models-serving"
'
# Look for entries with model_type:"llm" AND model_name (or fall back
# to isvc_name) AND served_model_url.

# 4. Flowise listModels itself returns the entry (full auth simulation)?
kubectl exec -n kubeflow flowise-0 -c flowise -- sh -c '
  C=/tmp/c; rm -f $C
  curl -sS -c $C -X POST -H "Content-Type: application/json" \
    -H "kubeflow-userid: <user>" -d "{}" \
    "http://localhost:3000/flowise/api/v1/auth/resolve" -o /dev/null
  curl -sS -b $C -X POST -H "Content-Type: application/json" \
    -H "kubeflow-userid: <user>" -H "x-request-from: internal" \
    -d "{\"name\":\"chatOpenAICustom\",\"loadMethod\":\"listModels\",\"inputs\":{}}" \
    "http://localhost:3000/flowise/api/v1/node-load-method/chatOpenAICustom"
'
# An empty response here when (3) returned data → stale 30s component
# cache, or the loadMethod plumbing is broken.
```

## Pod image refresh gotcha

The flowise charm's StatefulSet uses `imagePullPolicy: IfNotPresent`.
`juju attach-resource flowise oci-image=hiroregistry/cog-flow:dev`
updates the pod spec, but kubelet keeps the cached `:dev` image and
the pod runs the **old** content even after roll. Symptom: pod ready,
expected feature missing.

Workaround: attach by digest, which forces a fresh pull.

```sh
DIGEST=$(curl -s "https://hub.docker.com/v2/repositories/hiroregistry/cog-flow/tags/dev" | jq -r .digest)
juju attach-resource flowise oci-image=docker.io/hiroregistry/cog-flow@$DIGEST
```

## Component-level 30s cache (UX gotcha)

`ChatOpenAICustom.ts` caches the `/models-serving` response per
`(cogapiUrl, userIdentity)` for 30s. If a user opens the dropdown
once while the data was empty (e.g. cogapi briefly returned `[]`),
they'll see empty for up to 30s even after the underlying data is
fixed. Either wait, or restart the Flowise pod to clear the in-memory
cache.

## Related code paths

- `packages/server/src/controllers/nodes/index.ts:getSingleNodeAsyncOptions`
  — extracts `trustedUserIdentity` from the request header.
- `packages/server/src/services/nodes/index.ts:getSingleNodeAsyncOptions`
  — passes it into the `loadMethods` options bag.
- `packages/components/nodes/chatmodels/ChatOpenAICustom/ChatOpenAICustom.ts`
  — `fetchCogapiLlms(cogapiUrl, userIdentity)` forwards as
  `kubeflow-userid` header.
- `packages/server/src/enterprise/middleware/passport/index.ts:auth/resolve`
  — `TRUSTED_AUTH_HEADER` auto-login.
- `cog-flow-package/src/charm.py:_get_cog_api_url` — composes
  `COG_API_URL` env from the relation.
