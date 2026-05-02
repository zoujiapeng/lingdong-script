"""Variable system — typed variable storage for DSL and flow execution."""
import re
from typing import Any, Optional


class VariableSystem:
    """Global variable storage for automation scripts.

    Supports:
    - Type-aware storage (str, int, float, bool, list, dict)
    - Variable interpolation in strings: {{var_name}}
    - Conditional expression evaluation
    - Pre/post operation hooks
    """

    def __init__(self):
        self._variables: dict[str, Any] = {}
        self._readonly: set[str] = set()

    def set(self, name: str, value: Any, readonly: bool = False):
        """Set a variable value."""
        if name in self._readonly:
            raise ValueError(f"Variable '{name}' is read-only")
        self._variables[name] = value
        if readonly:
            self._readonly.add(name)

    def get(self, name: str, default: Any = None) -> Any:
        """Get a variable value."""
        return self._variables.get(name, default)

    def delete(self, name: str):
        """Delete a variable."""
        if name in self._readonly:
            raise ValueError(f"Variable '{name}' is read-only")
        self._variables.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._variables

    def keys(self) -> list[str]:
        return list(self._variables.keys())

    def items(self) -> list[tuple[str, Any]]:
        return list(self._variables.items())

    def clear(self):
        self._variables.clear()
        self._readonly.clear()

    def import_dict(self, data: dict):
        """Import variables from a dictionary."""
        self._variables.update(data)

    def export_dict(self) -> dict:
        """Export all variables as a dictionary."""
        return dict(self._variables)

    def to_number(self, name: str) -> Optional[float]:
        """Get a variable converted to number."""
        val = self._variables.get(name)
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def to_bool(self, name: str) -> bool:
        """Get a variable as boolean."""
        val = self._variables.get(name, False)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ('true', 'yes', '1')
        if isinstance(val, (int, float)):
            return val != 0
        return bool(val)

    def interpolate(self, text: str) -> str:
        """Replace {{var_name}} placeholders with variable values.

        Supports:
        - Simple: {{var_name}}
        - Dot access: {{var_name.key}}
        - Default: {{var_name|default_value}}
        """
        def _replace(match):
            expr = match.group(1).strip()
            default = None

            if '|' in expr:
                expr, default = expr.split('|', 1)
                default = default.strip()

            # Dot access
            parts = expr.split('.')
            val = self._variables.get(parts[0])
            if val is not None:
                for part in parts[1:]:
                    if isinstance(val, dict):
                        val = val.get(part)
                    elif isinstance(val, (list, tuple)):
                        try:
                            val = val[int(part)]
                        except (ValueError, IndexError):
                            val = None
                            break
                    else:
                        val = None
                        break

            if val is not None:
                return str(val)
            return str(default) if default is not None else f'{{{{{expr}}}}}'

        return re.sub(r'\{\{(.+?)\}\}', _replace, text)

    def evaluate_condition(self, left: str, operator: str, right: str) -> bool:
        """Evaluate a condition expression.

        Args:
            left: Variable name or literal value.
            operator: '==', '!=', '>', '<', '>=', '<=', 'contains', 'exists'.
            right: Comparison value.

        Returns:
            Boolean result.
        """
        # Resolve variable references
        left_val = self.interpolate(left)
        if left_val == left and self.has(left):
            left_val = self.get(left)

        right_val = self.interpolate(right)
        if right_val == right and self.has(right):
            right_val = self.get(right)

        # Type-aware comparison
        try:
            left_num = float(left_val)
            right_num = float(right_val)
            use_numeric = True
        except (ValueError, TypeError):
            use_numeric = False

        if operator == '==':
            return str(left_val) == str(right_val)
        elif operator == '!=':
            return str(left_val) != str(right_val)
        elif operator == '>':
            if use_numeric:
                return left_num > right_num
            return str(left_val) > str(right_val)
        elif operator == '<':
            if use_numeric:
                return left_num < right_num
            return str(left_val) < str(right_val)
        elif operator == '>=':
            if use_numeric:
                return left_num >= right_num
            return str(left_val) >= str(right_val)
        elif operator == '<=':
            if use_numeric:
                return left_num <= right_num
            return str(left_val) <= str(right_val)
        elif operator == 'contains':
            return str(right_val) in str(left_val)
        elif operator == 'exists':
            return self.has(left)
        elif operator == 'not_exists':
            return not self.has(left)

        return False
