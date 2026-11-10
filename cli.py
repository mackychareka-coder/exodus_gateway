"""Flask CLI helpers:  flask --app app:create_app create-user / create-terminal"""
import click

import db
from auth import USER_ROLES, generate_api_key, hash_api_key, hash_password


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(USER_ROLES), required=True)
    @click.option("--employee-code", default=None, help="Required for EMPLOYEE role.")
    @click.password_option(confirmation_prompt=True)
    def create_user(username, role, employee_code, password):
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        with db.get_cursor() as cur:
            emp_id = None
            if employee_code:
                cur.execute("SELECT id FROM employees WHERE employee_code=%s", (employee_code.upper(),))
                row = cur.fetchone()
                if not row:
                    raise click.ClickException("Unknown employee code.")
                emp_id = row["id"]
            cur.execute("INSERT INTO users (username, password_hash, role, employee_id) VALUES (%s,%s,%s,%s)",
                        (username, hash_password(password), role, emp_id))
        click.echo(f"Created {role} user '{username}'.")

    @app.cli.command("create-terminal")
    @click.argument("name")
    @click.argument("gate_location")
    def create_terminal(name, gate_location):
        key = generate_api_key()
        with db.get_cursor() as cur:
            cur.execute("INSERT INTO terminals (name, gate_location, api_key_hash) VALUES (%s,%s,%s)",
                        (name, gate_location, hash_api_key(key)))
        click.echo(f"Terminal '{name}' created. API key (shown once): {key}")
