# Name of the project
PROJECT_NAME ?= kicad-builder
REGISTRY ?= ghcr.io/slintak
IMAGE ?= $(REGISTRY)/$(PROJECT_NAME)
KICAD_VERSION := $(shell sed -n 's/^ARG KICAD_VERSION=//p' Dockerfile)
BUILDER_VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' src/kicad_fab/__init__.py)
TAGS ?= latest $(KICAD_VERSION) $(KICAD_VERSION)-$(BUILDER_VERSION)
# Image tag for "make test", "make run", "make demo" and "make versions".
TAG ?= latest
DOCKER_USER = -u "$$(id -u):$$(id -g)" -e HOME=/tmp

.PHONY: build push run test demo versions clean

build:  ## Build the image with all tags
	docker build --pull -f Dockerfile \
		--build-arg KICAD_VERSION=$(KICAD_VERSION) --build-arg BUILDER_VERSION=$(BUILDER_VERSION) \
		$(foreach t,$(TAGS),-t $(IMAGE):$(t)) .

push:  ## Push the image tags to the registry
	$(foreach t,$(TAGS),docker push $(IMAGE):$(t) &&) true

run:  ## Shell in the image
	docker run --rm -it -v "$(CURDIR)":/workspace $(IMAGE):$(TAG) /bin/bash

test:  ## Run the tests in the image
	docker run --rm $(DOCKER_USER) -v "$(CURDIR)":/src -w /src $(IMAGE):$(TAG) python3 -m pytest -v

demo:  ## Make the demo board of the tests again
	docker run --rm $(DOCKER_USER) -v "$(CURDIR)":/src -w /src $(IMAGE):$(TAG) \
		python3 tools/make_demo_board.py tests/projects/demo

versions:  ## Print the versions in the image
	docker run --rm $(IMAGE):$(TAG) kicad-fab versions

clean:
	docker image prune -f
