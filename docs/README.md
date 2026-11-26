# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

## Installation

```bash
npm install
```

**Note**: feel free to use the package manager of your choice.

## Local Development

```bash
npm run start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

## Build

```bash
npm run build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service.

## Translations

English is the default; Simplified Chinese (`zh-Hans`) uses Docusaurus' own i18n. A translated page is
the same file path under `i18n/zh-Hans/docusaurus-plugin-content-docs/current/` (front matter and
prose translated; code, commands, setting names and links unchanged). A page with no translation
there is not built for that locale, so add the file or links to it break the build. Explicit heading
ids (`## Title {/* #id */}`) keep anchors that other pages link to stable across languages.

```bash
pnpm start --locale zh-Hans             # preview the translation
pnpm write-translations --locale zh-Hans   # refresh navbar, footer and sidebar strings
pnpm build                              # builds every locale
```

`reference/configuration.md` is generated in both languages: `uv run python scripts/export_settings_docs.py`.

## Deployment

Using SSH:

```bash
USE_SSH=true npm run deploy
```

Not using SSH:

```bash
GIT_USER=<Your GitHub username> npm run deploy
```

If you are using GitHub Pages for hosting, this command is a convenient way to build the website and push to the `gh-pages` branch.
