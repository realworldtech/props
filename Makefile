.PHONY: version release-pr

version:
	@echo "Last released: $$(git tag -l 'v*' --sort=-v:refname | head -1 || echo 'no releases yet')"

release-pr:
	@git fetch --tags origin main develop; \
	LAST_TAG=$$(git tag -l 'v*' --sort=-v:refname | head -1); \
	if [ -z "$$LAST_TAG" ]; then \
		DIFF_RANGE="main..develop"; \
	else \
		DIFF_RANGE="$$LAST_TAG..develop"; \
	fi; \
	COMMITS=$$(git log --oneline $$DIFF_RANGE); \
	if [ -z "$$COMMITS" ]; then \
		echo "No new commits on develop since last release."; \
		exit 1; \
	fi; \
	echo "Generating release PR description..."; \
	PR_BODY=$$(echo "$$COMMITS" | claude -p \
		--model haiku \
		--allowedTools "" \
		--max-turns 1 \
		"You are writing a GitHub pull request body for a release merge from develop to main. \
		Below are the commits being merged. Write a concise PR body in this exact format: \
		\
		## Summary \
		<2-4 bullet points summarising the changes, grouped by theme> \
		\
		## Commits \
		<list each commit as a bullet> \
		\
		Do not add any other sections. Do not wrap in markdown code fences. \
		Here are the commits:"); \
	YEAR_MONTH=$$(date -u +%Y.%m); \
	LATEST_PATCH=$$(git tag -l "v$$YEAR_MONTH.*" --sort=-v:refname | head -1 | sed "s/v$$YEAR_MONTH\.//"); \
	if [ -z "$$LATEST_PATCH" ]; then \
		NEXT_VERSION="$$YEAR_MONTH.0"; \
	else \
		NEXT_VERSION="$$YEAR_MONTH.$$((LATEST_PATCH + 1))"; \
	fi; \
	gh pr create --base main --head develop \
		--title "Release $$NEXT_VERSION" \
		--body "$$PR_BODY"
