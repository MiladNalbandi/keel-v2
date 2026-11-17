// The Database plugin's popup over the Code page: the same list and editor as Settings › Databases (DbManager, as a
// project's Databases shows them), so a connection is added or edited without leaving the Code page. It opens on a new
// draft (the side bar's "+", "Add one") or on one connection (its ⚙). A save or a remove tells the side bar to read its
// connections again; Save (not Apply), the editor's Cancel, Esc and Close close it.
// The Database plugin's part (plugins/db): it imports only react and @keel/web-sdk.

import { useEffect } from "react";
import { Drawer, useApp } from "@keel/web-sdk";
import { DbManager } from "./DbManager";

export type DbPopupOpen = { select?: string };

export function DbPopup({
  pid,
  select,
  onClose,
  onChanged,
}: {
  pid: string;
  /** the connection to open on, by name; none: a new draft */
  select?: string;
  onClose: () => void;
  /** after a save or a remove: the side bar reloads */
  onChanged: () => void;
}) {
  const { project } = useApp();
  const name = project?.name ?? pid;
  // the Drawer focuses the first field (the list's filter); a new draft starts on its Name instead
  useEffect(() => {
    if (select) return;
    document
      .querySelector<HTMLInputElement>("#db-popup form.dbx-editor input")
      ?.focus();
  }, [select]);
  return (
    <Drawer title={`Databases of ${name}`} onClose={onClose} id="db-popup">
      <DbManager
        pid={pid}
        projectName={name}
        place="project"
        startNew={!select}
        select={select}
        onCancel={onClose}
        onChanged={(closed) => {
          onChanged();
          if (closed) onClose();
        }}
      />
    </Drawer>
  );
}
