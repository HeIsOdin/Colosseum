import os
import re

def env(vars: str, defaults: str = '', delimiter: str = ",") -> tuple[str, ...]:
    """
    Retrieve environment variables.

    Args:
        - vars      (str) : variables set in the environment
        - defaults  (str) : default values for the variables, separated by the specified delimiter
        - delimiter (str) : the character used to separate default values in the defaults string
    
    Returns:
        tuple (number of arguments passed): values of environmental variables
    """
    values: list[str] = []
    l_vars = vars.split(delimiter); l_defaults = defaults.split(delimiter)
    while len(l_defaults) < len(l_vars): l_defaults.append('') # Pad defaults with empty strings if not enough provided
    for var, default in zip(l_vars, l_defaults):
        value = os.getenv(var.strip())
        if value: values.append(value)
        elif default: values.append(default.strip())
    
    if len(values) != len(l_vars):
        raise Exception(f"Some or all values in {vars} not set in environment without defaults.")

    return tuple(values)
            
def raise_on_invalid_creds(email: str, password: str):
    """
    Check if the provided email and password are valid credentials in the database.

    Args:
        - email (str) : The email to check.
        - password (str) : The password to check.
    Returns:
        bool: True if the credentials are valid, False otherwise.
    """
    email = email.strip()
    password = password.strip()
    email_pattern = r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
    if not email or not password: raise ValueError("Email and password must not be empty.")
    if len(password) < 8: raise ValueError("Password must be at least 8 characters long.")
    if not re.match(email_pattern, email): raise ValueError("Invalid email format.")