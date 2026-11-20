"""`val3 view`: the map endpoint, on vectors written straight into a vault."""
from helpers import OUTPUT, fact, make_vault, recipe, save
from viewer import viewing

SPACE = ("m", "d1", 1, 3)


def vectors(env, rows, space=SPACE):
    model, digest, version, dims = space
    for entry_id, vector in rows:
        env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
                "VALUES (%s, %s, %s, %s, %s, %s::vector)", (entry_id, model, digest, version, dims, str(vector)))


def saved(env, n):
    transcript = env.transcript(tool_outputs=[OUTPUT])
    for i in range(1, n + 1):
        result = save(env, fact(title=f"Fact {i}", statement=f"Statement {i}."), transcript)
        assert result.stdout == f"admitted {env.vault}#{i}\n", result.stderr


def test_nearest_neighbours_within_one_space(env):
    make_vault(env)
    saved(env, 6)
    vectors(env, [(1, [1, 0, 0]), (2, [0.9, 0.1, 0]), (3, [0.8, 0.2, 0]), (4, [0, 1, 0]), (5, [0.1, 0.9, 0])])
    vectors(env, [(6, [1, 0, 0])], space=("other", "d2", 1, 3))  # another space: never compared
    with viewing(env) as view:
        data = view.json("api/map")
        assert data["computed_at"].endswith("+00:00")
        answer = data["vault"]
        assert answer["ok"] and answer["name"] == env.vault
        assert (answer["total"], answer["shown"], answer["left_out"], answer["without_vector"]) == (6, 6, 0, 1)
        assert answer["space"] == {"model": "m", "digest": "d1", "text_version": 1, "dims": 3}
        assert [n["ref"] for n in answer["nodes"]] == [f"{env.vault}#{i}" for i in (6, 5, 4, 3, 2, 1)]
        near = {}
        for edge in answer["edges"]:
            assert edge["type"] == "near"
            near.setdefault(edge["from"], []).append(edge["to"])
        ref = lambda i: f"{env.vault}#{i}"  # noqa: E731
        assert near[ref(1)] == [ref(2), ref(3), ref(5)]
        assert near[ref(4)] == [ref(5), ref(3), ref(2)]
        assert ref(6) not in near
        vectors(env, [(6, [0, 0, 1])])
        again = view.json("api/map")
        assert again["computed_at"] == data["computed_at"] and again["vault"] == answer  # kept for 10 minutes
        assert view.json("api/map?vault=other", expect=404)["error"]
        assert view.json("api/map?vault=x&vault=y", expect=400)["error"]


def test_supersessions_and_citations_are_edges(env):
    make_vault(env)
    saved(env, 2)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    newer = fact(title="Fact 3", statement="Statement 3.", supersedes=[f"{env.vault}#1"])
    assert save(env, newer, transcript).stdout == f"admitted {env.vault}#3\n"
    result = save(env, recipe(f"{env.vault}#2", evidence=fact()["evidence"]), transcript)
    assert result.returncode == 0, result.stderr
    with viewing(env) as view:
        answer = view.json("api/map")["vault"]
    assert answer["space"] is None and answer["without_vector"] == 4
    assert {(e["from"], e["to"], e["type"]) for e in answer["edges"]} == {
        (f"{env.vault}#3", f"{env.vault}#1", "supersedes"), (f"{env.vault}#4", f"{env.vault}#2", "cites")}


def test_the_most_recent_entries_only(env):
    make_vault(env)
    env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "SELECT 'fact', 'Fact ' || i, 'Statement ' || i, md5(i::text), 'project', 'm' "
            "FROM generate_series(1, 1510) i")
    env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
            "SELECT id, 'm', 'd1', 1, 3, ('[' || (id %% 7) || ',' || (id %% 5) || ',1]')::vector "
            f"FROM {env.vault}.entry WHERE id > 20")
    with viewing(env) as view:
        answer = view.json("api/map")["vault"]
    assert (answer["total"], answer["shown"], answer["left_out"], answer["without_vector"]) == (1510, 1500, 10, 10)
    assert answer["nodes"][0]["ref"] == f"{env.vault}#1510" and answer["nodes"][-1]["ref"] == f"{env.vault}#11"
    assert sum(e["type"] == "near" for e in answer["edges"]) == 1490 * 3
