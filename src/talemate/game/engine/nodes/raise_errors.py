import structlog
import pydantic

import talemate.exceptions as exceptions
import talemate.scene_message as scene_message
from talemate.context import active_scene, interaction
from talemate.game.engine.nodes.core import (
    Node,
    register,
    GraphState,
    UNRESOLVED,
    PropertyField,
    InputValueError,
    NodeStyle,
    StopGraphExecution,
    StopModule,
    LoopBreak,
    LoopContinue,
    LoopExit,
)

__all__ = [
    "ActedAsCharacter",
    "InputValueErrorNode",
    "Stop",
]

log = structlog.get_logger("talemate.game.engine.nodes.core.raise")


@register("raise/ActedAsCharacter")
class ActedAsCharacter(Node):
    """
    Raises an ActedAsCharacter exception.

    This is used to communicate to the main scene loop that the user
    has performed an action as a specific character.

    Inputs:

    - state: The current graph state
    - character_name: The name of the character the user acted as
    - advance_scene: Whether acting as the character should advance the scene
    """

    def __init__(self, title="Acted As Character", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("character_name", socket_type="str")
        self.add_input("advance_scene", socket_type="bool", optional=True)

    def _interaction_state(self, state: GraphState):
        current_state = state
        while current_state:
            interaction_state = current_state.data.get("interaction_state")
            if interaction_state:
                return interaction_state
            current_state = current_state.outer
        return interaction.get()

    def _mark_latest_advancing_message(self, character_name: str | None) -> str | None:
        scene = active_scene.get()
        if not scene:
            return character_name

        for message in reversed(scene.history):
            if (
                isinstance(message, scene_message.CharacterMessage)
                and message.source == "player"
                and (
                    character_name is None
                    or message.character_name == character_name
                )
            ):
                message.set_meta(advances_scene_as_character=True)
                return message.character_name

        return character_name

    async def run(self, state: GraphState):
        character_name = self.get_input_value("character_name")
        advance_scene = self.normalized_input_value("advance_scene")
        interaction_state = self._interaction_state(state)

        if character_name is UNRESOLVED:
            character_name = getattr(interaction_state, "act_as", None)

        if advance_scene is None:
            advance_scene = getattr(interaction_state, "advance_scene", False)

        if advance_scene:
            character_name = self._mark_latest_advancing_message(character_name)
            scene = active_scene.get()
            state.shared["signal_game_loop"] = True
            if scene and character_name:
                state.shared["acted_as_character"] = scene.get_character(character_name)
            return

        raise exceptions.ActedAsCharacter(
            character_name,
            advance_scene=bool(advance_scene),
        )


@register("raise/Stop")
class Stop(Node):
    """
    Raises the sepcified node / scene loop exception
    to stop execution of the current graph

    Inputs:

    - state: The current state
    - exception: The exception to raise

    Outputs:

    - state: The current state
    """

    @pydantic.computed_field(description="Node style")
    @property
    def style(self) -> NodeStyle:
        return NodeStyle(
            node_color="#401a1a",
            title_color="#111",
            icon="F0028",  # alert-circle
        )

    class Fields:
        exception = PropertyField(
            name="exception",
            description="Exception",
            type="str",
            default="StopGraphExecution",
            choices=[
                "StopGraphExecution",
                "StopModule",
                "LoopBreak",
                "LoopContinue",
                "LoopExit",
                "ExitScene",
                "RestartSceneLoop",
                "ResetScene",
                "StageExit",
            ],
        )

    def __init__(self, title="Stop", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("exception", type="str", optional=True)

        self.set_property("exception", "StopGraphExecution")

        self.add_output("state")

    async def run(self, state: GraphState):
        exception = self.require_input("exception")

        # this will never be reached, but it's here to make sure the
        # that Stage nodes can be connected to this node
        self.set_output_values({"state": self.get_input_value("state")})

        if exception == "StopGraphExecution":
            raise StopGraphExecution()
        elif exception == "StopModule":
            raise StopModule()
        elif exception == "LoopBreak":
            raise LoopBreak()
        elif exception == "LoopContinue":
            raise LoopContinue()
        elif exception == "LoopExit":
            raise LoopExit()
        elif exception == "ExitScene":
            raise exceptions.ExitScene()
        elif exception == "RestartSceneLoop":
            raise exceptions.RestartSceneLoop()
        elif exception == "ResetScene":
            raise exceptions.ResetScene()
        else:
            raise InputValueError(self, "exception", f"Unknown exception: {exception}")


@register("raise/InputValueError")
class InputValueErrorNode(Node):
    """
    Raises an InputValueError exception.

    Inputs:

    - state: The current state
    - field: The field that caused the error
    - message: The message to raise the exception with
    """

    @pydantic.computed_field(description="Node style")
    @property
    def style(self) -> NodeStyle:
        return NodeStyle(
            node_color="#401a1a",
            title_color="#111",
            icon="F0028",  # alert-circle
        )

    class Fields:
        message = PropertyField(
            name="message",
            description="The message to raise the exception with",
            type="str",
            default="",
        )

        field = PropertyField(
            name="field",
            description="The field that caused the error",
            type="str",
            default="",
        )

    def __init__(self, title="Input Value Error", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("message", socket_type="str")
        self.add_input("field", socket_type="str")

        self.set_property("message", "")
        self.set_property("field", "")

        self.add_output("state")

    async def run(self, state: GraphState):
        message = self.require_input("message")
        field = self.require_input("field")

        # this will never be reached, but it's here to make sure the
        # that Stage nodes can be connected to this node
        self.set_output_values({"state": self.get_input_value("state")})

        raise InputValueError(self, field, message)
