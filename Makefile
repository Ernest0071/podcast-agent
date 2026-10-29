.PHONY: build run run-ep test docker-test

build:
	docker compose build

run:
	docker compose run --rm agent

# make run-ep EP=ep002
run-ep:
	docker compose run --rm agent run --episodes $(EP)

test:
	docker compose run --rm --no-deps --entrypoint pytest agent -q

docker-test: test
