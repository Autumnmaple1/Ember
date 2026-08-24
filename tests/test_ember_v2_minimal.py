from ember_v2 import Agent, AgentKind, Environment, Runtime


def make_runtime() -> Runtime:
    runtime = Runtime()
    runtime.add_environment(
        Environment(
            id="room",
            name="默认房间",
            description="一个安静的房间。",
        )
    )
    runtime.add_agent(
        Agent(
            id="player",
            name="玩家",
            kind=AgentKind.PLAYER,
            persona="外部玩家输入。",
            environment_id="room",
        )
    )
    runtime.add_agent(
        Agent(
            id="ember",
            name="Ember",
            kind=AgentKind.AI,
            persona="温柔、敏锐的 AI 伴侣。",
            environment_id="room",
            idle_timeout=5.0,
        )
    )
    return runtime


def test_player_message_triggers_one_ai_reply() -> None:
    runtime = make_runtime()

    reply = runtime.player_speak("player", "你好")

    assert reply is not None
    environment = runtime.environments["room"]
    assert len(environment.history) == 2
    assert environment.history[0].speaker_id == "player"
    assert environment.history[1].speaker_id == "ember"
    assert len(runtime.environment_updates) == 2
    assert runtime.environment_updates[0].allow_ai_reply is True
    assert runtime.environment_updates[1].allow_ai_reply is False
    assert environment.summary.startswith("v2: ember said")


def test_tick_only_triggers_timed_out_ai_agent() -> None:
    runtime = make_runtime()

    early = runtime.tick(4.0)
    late = runtime.tick(1.0)

    assert early == []
    assert len(late) == 1
    assert late[0].speaker_id == "ember"
    assert len(runtime.environment_updates) == 1
    assert runtime.environment_updates[0].allow_ai_reply is False
    assert runtime.environments["room"].summary.startswith("v1: ember said")


def test_environment_history_is_observed_by_agents_in_same_environment() -> None:
    runtime = make_runtime()

    runtime.player_speak("player", "你在吗")

    player = runtime.agents["player"]
    ember = runtime.agents["ember"]
    assert len(player.short_term_memory) == 2
    assert len(ember.short_term_memory) == 2
