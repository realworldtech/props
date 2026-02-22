.PHONY: version release-pr

version:
	@echo "Last released: $$(git tag -l 'v*' --sort=-v:refname | head -1 || echo 'no releases yet')"

release-pr:
	gh pr create --base main --head develop \
		--title "Release $$(date -u +%Y.%m)" \
		--body "Merge develop into main to trigger a new release."
