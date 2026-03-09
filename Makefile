# Makefile for Flowise Charm
# Sets required environment variables for charmcraft builds

# Required: Use charmcraft 3.x snap in LXD container (4.x has bugs)
export CRAFT_SNAP_CHANNEL := 3.x/stable

.PHONY: all pack clean fetch-libs test upload release help

help:
	@echo "Flowise Charm Build Targets:"
	@echo "  make pack        - Fetch libs and pack the charm"
	@echo "  make fetch-libs  - Fetch charm libraries from Charmhub"
	@echo "  make clean       - Clean build artifacts"
	@echo "  make test        - Run unit tests"
	@echo "  make upload      - Upload charm to Charmhub"
	@echo "  make release     - Release to latest/edge (requires REVISION=N)"

all: pack

fetch-libs:
	charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
	charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
	charmcraft fetch-lib charms.data_platform_libs.v0.s3
	charmcraft fetch-lib charms.redis_k8s.v0.redis

pack: fetch-libs
	charmcraft pack

clean:
	charmcraft clean
	rm -rf parts/ *.charm

test:
	pip install -q ops pytest
	python -m pytest tests/unit/ -v

upload:
	charmcraft upload flowise_ubuntu-22.04-amd64.charm

release:
ifndef REVISION
	$(error REVISION is required. Usage: make release REVISION=3)
endif
	charmcraft release flowise --revision=$(REVISION) --channel=latest/edge --resource=oci-image:1
