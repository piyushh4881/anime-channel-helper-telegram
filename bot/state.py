from database.db import async_session
import database.crud as crud


class UserState:
    @staticmethod
    async def set_state(user_id: int, state: str | None, state_data: dict = None):
        """Sets the FSM state and data for a user."""
        async with async_session() as db:
            await crud.update_user_state(db, user_id, state, state_data)

    @staticmethod
    async def get_state(user_id: int) -> tuple[str | None, dict | None]:
        """Returns the current FSM state and data for a user as (state, state_data)."""
        async with async_session() as db:
            user = await crud.get_user(db, user_id)
            if user:
                return user.state, user.state_data
            return None, None

    @staticmethod
    async def update_data(user_id: int, **kwargs):
        """Updates FSM data key-value pairs without changing the state string."""
        async with async_session() as db:
            user = await crud.get_user(db, user_id)
            if user:
                current_data = dict(user.state_data or {})
                current_data.update(kwargs)
                await crud.update_user_state(db, user_id, user.state, current_data)

    @staticmethod
    async def clear_state(user_id: int):
        """Clears state and state data for a user."""
        async with async_session() as db:
            await crud.update_user_state(db, user_id, None, None)
