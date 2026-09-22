.PHONY: test check install

test:
	bash scripts/run-test.sh

check:
	bash scripts/check.sh

install:
	sudo scripts/install.sh
