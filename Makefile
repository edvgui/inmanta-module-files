isort = isort inmanta_plugins tests inmanta_files
black_preview = black --preview inmanta_plugins tests inmanta_files
black = black inmanta_plugins tests inmanta_files
flake8 = flake8 inmanta_plugins tests inmanta_files
pyupgrade = pyupgrade --py312-plus $$(find inmanta_plugins tests inmanta_files -type f -name '*.py')

format:
	$(isort)
	$(black_preview)
	$(black)
	$(flake8)
	$(pyupgrade)

install:
	uv pip install -U -r requirements.dev.txt -c requirements.txt -e .

.PHONY: pep8
pep8:
	$(flake8)
	$(pyupgrade)

SET_UP_MYPY_PLUGINS=mkdir -p mypy/out
RUN_MYPY_PLUGINS=python -m mypy --namespace-packages -p inmanta_plugins.files
RUN_MYPY_TESTS=MYPYPATH=tests python -m mypy --html-report mypy/out/tests tests

mypy-plugins:
	@ echo -e "Running mypy on the module plugins\n..."
	@ $(SET_UP_MYPY_PLUGINS)
	@ $(RUN_MYPY_PLUGINS) --html-report mypy/out/inmanta_plugins

mypy-tests:
	@ echo -e "Running mypy on the module tests\n..."
	@ $(SET_UP_MYPY_PLUGINS)
	@ $(RUN_MYPY_TESTS)

.PHONY: mypy
mypy: mypy-plugins mypy-tests

mypy-baseline:
	$(RUN_MYPY_PLUGINS) | mypy-baseline filter

mypy-baseline-sync:
	$(RUN_MYPY_PLUGINS) | mypy-baseline sync

ci-mypy: mypy-baseline
