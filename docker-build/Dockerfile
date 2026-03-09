# Custom Flowise image with BASE_PATH support
# This patches the pre-built Flowise to support reverse proxy with path prefix

FROM flowiseai/flowise:latest

USER root

# Install sed for patching and make files writable for patching at runtime
RUN apk add --no-cache sed && \
    chmod -R a+w /usr/local/lib/node_modules/flowise/node_modules/flowise-ui/build && \
    chmod -R a+w /usr/local/lib/node_modules/flowise/dist && \
    mkdir -p /usr/local/lib/node_modules/flowise/logs && \
    chmod -R a+w /usr/local/lib/node_modules/flowise/logs

# Create entrypoint script that patches files for BASE_PATH support
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER node

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["flowise", "start"]
