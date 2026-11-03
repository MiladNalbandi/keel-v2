// The GitHub Issues plugin's web part (plugins/github-issues, issue #64): `npm run build:plugin -- github-issues` (in web/)
// builds this file on its own into plugins/github-issues/web/dist/index.js. keel loads it at start from the url
// /api/features gives and calls setup() once: it puts the GitHub Issues section in Settings › Connections, after Jira.
// It imports only @keel/web-sdk and react: keel's page shares its own copies (the import map).

import { definePlugin, SLOTS, type ConnectionKindItem, type KeelSdk } from "@keel/web-sdk";
import { GitHubIssuesSection } from "./GitHubIssuesCard";

export function setup(sdk: KeelSdk) {
  sdk.registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
    id: "github-issues",
    title: "GitHub Issues",
    order: 11,
    component: GitHubIssuesSection,
  });
}

export default definePlugin({ name: "github-issues", setup });
