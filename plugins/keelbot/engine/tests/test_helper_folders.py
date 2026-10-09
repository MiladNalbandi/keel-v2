"""v0.15.2 KeelBot's chat folders (keel_plugin_keelbot.helper): a folder per project, a chat in one folder or none,
rename, and delete that keeps the chats. The tables stay core's (runtime/migrate.py, engine/tests/test_helper_folders.py)."""

from keel_engine.runtime import db

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}


def new_session(client, repo, project="demo", **extra):
    r = client.post("/helper/sessions", json={"project_id": project, "root": str(repo), "model": FAKE, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def new_folder(client, name, project="demo"):
    return client.post("/helper/folders", json={"project_id": project, "name": name})


def test_folders_are_made_named_and_listed_per_project(client):
    r = new_folder(client, "  Payments   and refunds ")
    assert r.status_code == 200, r.text
    pay = r.json()
    assert pay["id"].startswith("hf_") and pay["name"] == "Payments and refunds" and pay["project"] == "demo" and pay["chats"] == 0
    new_folder(client, "Auth")
    new_folder(client, "Other project's", project="other")
    assert [f["name"] for f in client.get("/helper/folders", params={"project": "demo"}).json()] == ["Auth", "Payments and refunds"]

    # a name is needed, not too long, and one name once per project (any case)
    assert new_folder(client, "   ").status_code == 400
    assert new_folder(client, "x" * 61).status_code == 400
    dup = new_folder(client, "auth")
    assert dup.status_code == 409 and "exists already" in dup.json()["error"]
    assert new_folder(client, "Auth", project="other").status_code == 200

    # rename: the same rules, and its own name in another case is fine
    renamed = client.patch(f"/helper/folders/{pay['id']}", json={"name": "Payments"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Payments"
    assert client.patch(f"/helper/folders/{pay['id']}", json={"name": "AUTH"}).status_code == 409
    assert client.patch(f"/helper/folders/{pay['id']}", json={"name": "payments"}).json()["name"] == "payments"
    assert client.patch("/helper/folders/hf_nope", json={"name": "x"}).status_code == 404


def test_a_chat_moves_into_a_folder_and_out_without_jumping_in_the_list(client, repo):
    older = new_session(client, repo, title="Older chat")
    newer = new_session(client, repo, title="Newer chat")
    folder = new_folder(client, "Scores").json()
    with db.connect() as conn:                                             # the older chat was last used yesterday
        conn.execute("update helper_sessions set updated_at = ? where id = ?", ("2026-10-07T09:00:00Z", older["id"]))

    r = client.patch(f"/helper/sessions/{older['id']}", json={"folder": folder["id"]})
    assert r.status_code == 200 and r.json()["folder"] == folder["id"]
    listed = client.get("/helper/sessions", params={"project": "demo"}).json()
    assert [(s["id"], s["folder"]) for s in listed] == [(newer["id"], None), (older["id"], folder["id"])]
    assert listed[1]["updated_at"] == "2026-10-07T09:00:00Z"               # moving is not activity: the order stays
    assert client.get("/helper/folders", params={"project": "demo"}).json()[0]["chats"] == 1

    # a folder of another project, or one that does not exist, is not found
    theirs = new_folder(client, "Theirs", project="other").json()
    assert client.patch(f"/helper/sessions/{older['id']}", json={"folder": theirs["id"]}).status_code == 404
    assert client.patch(f"/helper/sessions/{older['id']}", json={"folder": "hf_nope"}).status_code == 404
    assert client.get(f"/helper/sessions/{older['id']}").json()["folder"] == folder["id"]

    # a title change leaves the folder alone; "" takes the chat out
    client.patch(f"/helper/sessions/{older['id']}", json={"title": "Renamed"})
    assert client.get(f"/helper/sessions/{older['id']}").json()["folder"] == folder["id"]
    assert client.patch(f"/helper/sessions/{older['id']}", json={"folder": ""}).json()["folder"] is None


def test_deleting_a_folder_keeps_its_chats(client, repo):
    s1 = new_session(client, repo, title="One")
    s2 = new_session(client, repo, title="Two")
    folder = new_folder(client, "Old work").json()
    for s in (s1, s2):
        client.patch(f"/helper/sessions/{s['id']}", json={"folder": folder["id"]})
    r = client.delete(f"/helper/folders/{folder['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True, "moved": 2}
    assert client.get("/helper/folders", params={"project": "demo"}).json() == []
    listed = client.get("/helper/sessions", params={"project": "demo"}).json()
    assert sorted(s["title"] for s in listed) == ["One", "Two"] and all(s["folder"] is None for s in listed)
    assert client.delete(f"/helper/folders/{folder['id']}").status_code == 404
