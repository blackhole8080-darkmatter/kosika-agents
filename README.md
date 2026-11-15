# kosika-agents

Every Kosika agent lives here. The [kosika](https://github.com/blackhole8080-darkmatter/kosika) repo keeps the language, its engines, the standard library and Studio; the agents are Kosika programs that run on it.

| Folder | What is in it |
| --- | --- |
| `agents/` | One folder per agent: coder, hands (kits, diary, team board, plan, learn, mascots), lookout, gatherer, desk, assistant, study, researcher, osbuilder, digest and the rest |
| `swarm/` | The coder swarm and the project team (`develop.kos`, `team.kos`, `mind.kos`) |
| `models/` | Kosika's virtual models: the free-model pool, the router (`choose.kos`) and training for its own small coder |
| `benchmarks/` | The coder, osbuild and researcher benchmarks, and the Python comparison |
| `tests/` | Every agent program must check and pass its own tests |

## Setting up

Keep this repo next to `kosika`, in one folder:

```
projects/
  kosika/
  kosika-agents/
```

One line, from that folder:

```
git clone https://github.com/blackhole8080-darkmatter/kosika-agents
```

The scripts find the engine in the `kosika` folder beside this one. If yours is somewhere else, point `KOSIKA_HOME` at it. Kosika Studio finds the agents the same way, or through `KOSIKA_AGENTS_HOME`.

Run an agent:

```
kosika run agents/researcher/main.kos --input ask.json
kosika studio agents
```

Run the tests:

```
python -m pytest tests
```

## History

These folders were moved out of `kosika` (`examples/agents`, `examples/self_improve`, `examples/virtual_models` and four benchmarks) with their full git history, so `git log` on any file still shows how it grew. The coder's to-do list stays in kosika as `CODER_TODO.md`, because its requests are changes to Kosika itself.

## License

MIT, like Kosika.
