isort = isort inmanta_plugins tests
black_preview = black --preview inmanta_plugins tests
black = black inmanta_plugins tests
flake8 = flake8 inmanta_plugins tests


format:
	$(isort)
	$(black_preview)
	$(black)
	$(flake8)
