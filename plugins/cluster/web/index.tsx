// The Cluster plugin's web part (plugins/cluster): `npm run build:plugin -- cluster` (in web/) builds this file on its own into
// plugins/cluster/web/dist/index.js. It puts the Cluster page in the menu (Run, after Record): the person's Kubernetes clusters, k9s
// style, with Workloads, Overview and Connect. It imports only @keel/web-sdk and react.

import { definePlugin, type KeelSdk } from "@keel/web-sdk";
import { ClusterPage } from "./ClusterPage";

export function setup(sdk: KeelSdk) {
  sdk.registerPage({
    id: "cluster",
    label: "Cluster",
    group: "watch",
    order: 40,
    needsProject: "optional",
    icon: (
      <>
        <path d="M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4z" />
        <path d="M12 8.2 15.3 10v4L12 15.8 8.7 14v-4z" />
        <path d="M12 2.8v5.4M20 7.4l-4.7 2.6M20 16.6 15.3 14M12 21.2v-5.4M4 16.6 8.7 14M4 7.4l4.7 2.6" />
      </>
    ),
    component: ClusterPage,
  });
}

export default definePlugin({ name: "cluster", setup });
