# site

A static site: an index with the install line, the watch replay of one
recorded run, and its result card. No network, no fonts or scripts from
outside; open `index.html` straight from the folder.

Build locally (from the repo root, needs `pip install -e . -e ./arena`
or just a clone, since `build.py` puts `src/` and `arena/src/` on the path):

    python site/build.py --out build/site

Output folder: `build/site/` (git-ignored) with `index.html`, `replay.html`,
`card.html` and `assets/`.

CI: `.github/workflows/site.yml` runs the same build and uploads the folder
as the `site` artifact on every pull request and push. It deploys to GitHub
Pages only on a push to `main` or a manual run, and only once a person has
enabled Pages in the repo settings and set the repository variable
`PAGES_ENABLED` to `true` in the same step; the workflow never enables Pages.
