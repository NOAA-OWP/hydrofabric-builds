"""Mocks a local instance for building runners"""

from typing import Any


class TaskInstance:
    """A Mock TaskInstance for local runners similar to Apache Airflow."""

    def __init__(self) -> None:
        """Initialize the TaskInstance with empty XCom storage."""
        self.xcom_storage: dict[str, Any] = {}

    def xcom_push(self, key: str, value: Any) -> None:
        """
        Store a value in XCom for retrieval by downstream tasks.

        Parameters
        ----------
        key : str
            Unique identifier for the stored value. Convention is to use '{task_id}.{key_name}' format for namespacing.
        value : Any
            The data to store. Can be any Python object.
        """
        self.xcom_storage[key] = value

    def xcom_pull(self, task_id: str, key: str = "return_value") -> Any:
        """
        Retrieve a value from XCom that was pushed by an upstream task.

        Parameters
        ----------
        task_id : str
            The task_id of the task that pushed the value.
        key : str, default='return_value'
            The key used when the value was pushed. Default 'return_value' is used for values returned from task functions.

        Returns
        -------
        Any
            The stored value, or None if the key doesn't exist.
        """
        return self.xcom_storage.get(f"{task_id}.{key}")
