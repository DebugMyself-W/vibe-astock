# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- Create an issue with `gh issue create --title "..." --body "..."`.
- Read an issue with `gh issue view <number> --comments`, including labels and relevant comments.
- List issues with `gh issue list` and the appropriate state and label filters.
- Comment with `gh issue comment <number> --body "..."`.
- Apply or remove labels with `gh issue edit`.
- Close an issue with `gh issue close <number> --comment "..."`.

Infer the repository from the configured GitHub remote when running inside this clone.

## Skill terminology

When a skill says to publish to the issue tracker, create a GitHub issue. When it says to fetch the relevant ticket, read that GitHub issue and its comments.
