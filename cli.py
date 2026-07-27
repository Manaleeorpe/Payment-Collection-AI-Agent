from __future__ import annotations

from agent import Agent, MSG, State


def main() -> None:
    agent = Agent()
    print(f"Agent: {MSG['greet']}")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue

        response = agent.next(user_input)
        print(f"Agent: {response['message']}")

        if agent.state in (State.COMPLETE, State.CLOSED):
            break


if __name__ == "__main__":
    main()
