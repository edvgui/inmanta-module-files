isort = isort inmanta_plugins tests inmanta_files
black_preview = black --preview inmanta_plugins tests inmanta_files
black = black inmanta_plugins tests inmanta_files
flake8 = flake8 inmanta_plugins tests inmanta_files

format:
	$(isort)
	$(black_preview)
	$(black)
	$(flake8)

install:
	pip install -U pip setuptools
	pip install -U --upgrade-strategy=eager -r requirements.dev.txt -c requirements.txt -e .
