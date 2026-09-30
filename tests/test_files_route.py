def test_files_route_cannot_escape_temp(client, workdir):
    (workdir / "secret.txt").write_text("nope")
    # %2F decodes to "/" after routing, so this asks for temp/../secret.txt.
    r = client.get("/files/..%2Fsecret.txt")
    assert r.status_code == 404
