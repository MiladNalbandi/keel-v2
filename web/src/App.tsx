// Root: providers, shell, and the hash router. The pages come from the page registry: keel's own (corePages.ts) and
// the parts' (web/src/builtins.ts, loaded once by main.tsx), and the plugins' (their web parts, loaded at start by
// addons.ts).

import { Suspense, useEffect } from "react";
import { addonPage, useFeatures, useView } from "./addons";
import "./corePages";
import { Shell } from "./components/Shell";
import { NoProject } from "./components/ui";
import { FlowPage } from "./pages/Flow";
import { ProjectsPage } from "./pages/Projects";
import { SCREEN } from "./routes";
import { PageBoundary } from "./components/PageBoundary";
import { SignIn, useSignIn } from "./components/SignIn";
import { pageOf, usePages } from "./sdk/registry";
import { AppProvider, useApp, useRoute } from "./state";

/** v0.13.0 an add-on's page (keel Product: #/initiatives): when its part of keel is on; Flow otherwise, as for any
 *  unknown page. A link to a plugin's page (#/map) lands here until the plugin's web part has registered it at start:
 *  it waits for that, then the router reads the link again. */
function AddonRoute({
  screen,
  arg,
  pid,
}: {
  screen: string;
  arg?: string;
  pid: string;
}) {
  const features = useFeatures();
  if (!features.loaded || !features.started)
    return (
      <div className="empty loading" role="status">
        Loading…
      </div>
    );
  const s = features.screens.find((x) => x.id === screen);
  if (!s)
    return pid ? (
      <PageBoundary resetKey={`flow:${pid}`}>
        <FlowPage key={pid} pid={pid} />
      </PageBoundary>
    ) : (
      <NoProject />
    );
  if (s.needs_project && !pid) return <NoProject />;
  const Page = addonPage(s.addon, s.id);
  return (
    <PageBoundary resetKey={`${s.addon}:${s.id}:${pid}`}>
      <Suspense
        fallback={
          <div className="empty loading" role="status">
            Loading…
          </div>
        }
      >
        <Page pid={pid} arg={arg} />
      </Suspense>
    </PageBoundary>
  );
}

function Router() {
  const route = useRoute();
  const { page } = route;
  const { pid, projectsLoaded, projectsError } = useApp();
  // a page that registers later (a plugin's) shows as soon as it is there
  usePages();
  useEffect(() => {
    try {
      window.scrollTo(0, 0);
    } catch {
      /* jsdom */
    }
  }, [page]);
  // keel 0.18.57 an old link (#/budget, #/plugins/git) opens its Settings tab already; the address bar shows the new
  // link, in place of the old one (Back does not come here again)
  useEffect(() => {
    if (route.redirect && location.hash !== route.redirect) {
      try {
        history.replaceState(history.state, "", route.redirect);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch {
        location.replace(route.redirect);
      }
    }
  }, [route.redirect]);
  // a Product-only keel (or the Product view) has no Flow page: the start page (an empty link, Home) and a link to Flow
  // open Product's first page, else the Inbox (Home's box starts dev flows)
  const features = useFeatures();
  const view = useView();
  const devShown = features.parts.dev !== false && (features.mode !== "both" || view !== "product");
  const landing = features.loaded && !devShown ? (features.screens[0]?.id ?? SCREEN.inbox) : null;
  useEffect(() => {
    if ((page === SCREEN.flow || page === SCREEN.home) && landing) location.replace(`#/${landing}`);
  }, [page, landing]);
  if ((page === SCREEN.flow || page === SCREEN.home) && landing)
    return (
      <div className="empty loading" role="status">
        Loading…
      </div>
    );
  if (page === SCREEN.projects)
    return (
      <PageBoundary resetKey={page}>
        <ProjectsPage />
      </PageBoundary>
    );
  if (page === SCREEN.addon && route.screen) {
    if (!pid && !projectsLoaded)
      return (
        <div className="empty loading" role="status">
          Loading…
        </div>
      );
    return <AddonRoute screen={route.screen} arg={route.arg} pid={pid ?? ""} />;
  }
  const reg = pageOf(page);
  // a page that spans every project (the Inbox, Quality) needs no chosen one
  if (reg?.needsProject === false) {
    const Page = reg.component;
    return (
      <PageBoundary resetKey={page}>
        <Page pid={pid ?? ""} />
      </PageBoundary>
    );
  }
  // Connections and General settings work without a project, but every other screen needs one.
  if (!pid) {
    if (!projectsLoaded)
      return (
        <div className="empty loading" role="status">
          Loading…
        </div>
      );
    if (reg?.needsProject === "optional") {
      const Page = reg.component;
      return (
        <PageBoundary resetKey={page}>
          <Page pid="" />
        </PageBoundary>
      );
    }
    return (
      <>
        {projectsError && (
          <div className="errbox" role="alert" style={{ marginBottom: 16 }}>
            <b>{projectsError}</b>
          </div>
        )}
        <NoProject />
      </>
    );
  }
  const Page = reg?.component ?? FlowPage;
  return (
    <PageBoundary resetKey={`${page}:${pid}`}>
      <Page key={pid} pid={pid} />
    </PageBoundary>
  );
}

export function App() {
  // v0.18.1 not signed in (api auth/LocalAuth): one screen that says how, instead of every page failing
  const signIn = useSignIn();
  if (signIn.needed) return <SignIn state={signIn} />;
  return (
    <AppProvider>
      <Shell>
        <Router />
      </Shell>
    </AppProvider>
  );
}
