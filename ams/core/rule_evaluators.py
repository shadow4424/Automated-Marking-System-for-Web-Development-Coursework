"""Per-component rule evaluation functions for static requirement checks.

Each ``_evaluate_<component>_rule`` function inspects raw file content for
patterns described by a :class:`RequiredRule` and returns ``(count, passed)``.
"""

from __future__ import annotations

from ams.assessors.html_parser import TagCountingParser
from ams.core.profiles import RequiredRule


def _evaluate_html_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the html rule."""
    parser = TagCountingParser()
    parser.feed(content)
    selector = rule.selector.lower()
    if selector == "!doctype" or rule.id == "html.has_doctype":
        count = 1 if parser.has_doctype else 0
        return count, count >= rule.min_count
    if selector == "semantic" or rule.id == "html.has_semantic_structure":
        count = 1 if parser.has_semantic else 0
        return count, count >= rule.min_count
    if selector == "heading" or rule.id == "html.has_heading_hierarchy":
        count = 1 if parser.has_heading else 0
        return count, count >= rule.min_count
    if selector == "list" or rule.id == "html.has_lists":
        count = 1 if parser.has_list else 0
        return count, count >= rule.min_count
    if selector == "meta_charset" or rule.id == "html.has_meta_charset":
        count = 1 if parser.has_meta_charset else 0
        return count, count >= rule.min_count
    if selector == "meta_viewport" or rule.id == "html.has_meta_viewport":
        count = 1 if parser.has_meta_viewport else 0
        return count, count >= rule.min_count
    if selector == "html_lang" or rule.id == "html.has_lang_attribute":
        count = 1 if parser.has_html_lang else 0
        return count, count >= rule.min_count
    if selector == "img_alt" or rule.id == "html.has_alt_attributes":
        if parser.img_count == 0:
            return 1, True
        count = parser.img_with_alt
        return count, parser.img_with_alt == parser.img_count
    if selector == "label" or rule.id == "html.has_labels":
        count = parser.label_count
        return count, count >= rule.min_count
    if selector == "img" or rule.id == "html.has_image":
        count = parser.img_count
        return count, count >= rule.min_count
    if selector == "link_stylesheet" or rule.id == "html.links_stylesheet":
        count = parser.link_stylesheet_count
        return count, count >= rule.min_count
    if selector == "link_script" or rule.id == "html.links_script_or_js":
        count = parser.script_count
        return count, count >= rule.min_count
    count = parser.counts.get(selector, 0)
    return count, count >= rule.min_count


def _evaluate_css_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the css rule."""
    lowered = content.lower()
    brace_count = content.count("{")
    needle = rule.needle.lower()
    if needle == "{":
        return brace_count, brace_count >= rule.min_count
    if needle == "multiple_rules" or rule.id == "css.has_multiple_rules":
        return brace_count, brace_count >= rule.min_count
    if needle == "element_selector" or rule.id == "css.has_element_selector":
        selectors = ["body", "html", "h1", "h2", "h3", "p", "a", "div", "form", "input", "button", "nav", "main", "section"]
        count = sum(1 for selector in selectors if selector in lowered)
        return count, count >= rule.min_count
    if needle == "layout" or rule.id == "css.has_layout":
        props = ["margin", "padding", "display", "position", "width", "height", "top", "left", "right", "bottom"]
        count = sum(1 for item in props if item in lowered)
        return count, count >= rule.min_count
    if needle == "flexbox" or rule.id == "css.has_flexbox":
        count = 1 if ("display: flex" in lowered or "display:flex" in lowered) else 0
        return count, count >= rule.min_count
    if needle == "grid" or rule.id == "css.has_grid":
        count = 1 if ("display: grid" in lowered or "display:grid" in lowered) else 0
        return count, count >= rule.min_count
    if needle == "typography" or rule.id == "css.has_typography":
        props = ["font-family", "font-size", "line-height", "font-weight", "letter-spacing", "text-align"]
        count = sum(1 for item in props if item in lowered)
        return count, count >= rule.min_count
    if needle == "custom_properties" or rule.id == "css.has_custom_properties":
        count = content.count("--")
        return count, count >= rule.min_count
    if needle == "comments" or rule.id == "css.has_comments":
        count = content.count("/*")
        return count, count >= rule.min_count
    if needle == "universal_reset" or rule.id == "css.has_universal_reset":
        has_star = "* {" in lowered or "*{" in lowered
        has_box_sizing = "box-sizing" in lowered
        has_margin_reset = "margin: 0" in lowered or "margin:0" in lowered
        count = 1 if (has_star or has_box_sizing or has_margin_reset) else 0
        return count, count >= rule.min_count
    if needle == "parses_cleanly" or rule.id == "css.parses_cleanly":
        open_count = content.count("{")
        close_count = content.count("}")
        if open_count == 0:
            return 0, False
        imbalance = abs(open_count - close_count)
        if imbalance == 0:
            return 1, True
        return 1, False
    if needle == "body_card_layout" or rule.id == "css.body_card_layout":
        traits = [
            "max-width" in lowered,
            "margin: auto" in lowered or "margin:auto" in lowered or "0 auto" in lowered,
            "padding" in lowered,
            "box-shadow" in lowered,
            "border-radius" in lowered,
        ]
        count = sum(traits)
        return count, count >= 4
    if needle == "h1_styled" or rule.id == "css.h1_styled":
        has_h1 = "h1" in lowered
        has_color = "color" in lowered
        has_size = "font-size" in lowered or "font-weight" in lowered
        count = 1 if has_h1 and (has_color or has_size) else 0
        return count, count >= rule.min_count
    if needle == "table_profile_layout" or rule.id == "css.table_profile_layout":
        has_table = "table" in lowered
        has_width = "max-width" in lowered or ("width" in lowered and "table" in lowered)
        has_spacing = "border-spacing" in lowered or "border-collapse" in lowered
        count = 1 if (has_table and (has_width or has_spacing)) else 0
        return count, count >= rule.min_count
    if needle == "image_rounding_shadow" or rule.id == "css.image_rounding_shadow":
        count = sum(["border-radius" in lowered, "box-shadow" in lowered])
        return count, count >= rule.min_count
    if needle == "h2_section_style" or rule.id == "css.h2_section_style":
        has_h2 = "h2" in lowered
        has_color = "color" in lowered
        has_size = "font-size" in lowered
        count = 1 if has_h2 and (has_color or has_size) else 0
        return count, count >= rule.min_count
    if needle == "list_readability_style" or rule.id == "css.list_readability_style":
        has_list = "ul" in lowered or "li" in lowered or "ol" in lowered
        has_style = "list-style" in lowered
        has_spacing = "padding" in lowered or "margin" in lowered
        count = 1 if has_list and (has_style or has_spacing) else 0
        return count, count >= rule.min_count
    if needle == "link_hover_style" or rule.id == "css.link_hover_style":
        has_hover = "a:hover" in lowered or ":hover" in lowered
        count = 1 if has_hover else 0
        return count, count >= rule.min_count
    count = content.count(rule.needle)
    return count, count >= rule.min_count


def _evaluate_js_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the js rule."""
    lowered = content.lower()
    needle = rule.needle.lower()
    if needle == "dom_query" or rule.id == "js.has_dom_query":
        patterns = ["queryselector", "getelementbyid", "getelementsbyclass", "getelementsbytagname", "queryselectorall"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "dom_manipulation" or rule.id == "js.has_dom_manipulation":
        patterns = ["innerhtml", "textcontent", "appendchild", "removechild", "createelement", "setattribute", "classlist", "style."]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "loops" or rule.id == "js.has_loops":
        values = [
            "for " in lowered or "for(" in lowered,
            "while " in lowered or "while(" in lowered,
            ".foreach" in lowered,
            ".map(" in lowered,
        ]
        count = sum(values)
        return count, count >= rule.min_count
    if needle == "form_validation" or rule.id == "js.has_form_validation":
        patterns = [".value", "validity", "checkvalidity", "required", "pattern", ".length", "isnan", "typeof"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "async_patterns" or rule.id == "js.has_async_patterns":
        patterns = ["async ", "await ", "fetch(", "promise", ".then("]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "const_let" or rule.id == "js.has_const_let":
        count = (1 if "const " in lowered else 0) + (1 if "let " in lowered else 0)
        return count, count >= rule.min_count
    if needle == "`" or rule.id == "js.has_template_literals":
        count = content.count("`")
        return count, count >= rule.min_count
    if needle == "creates_display_dom" or rule.id == "js.creates_display_dom":
        has_thedisplay = "thedisplay" in lowered
        has_getelm_display = "getelementbyid" in lowered and "display" in lowered
        count = 1 if (has_thedisplay or has_getelm_display) else 0
        return count, count >= rule.min_count
    if needle == "creates_digit_buttons" or rule.id == "js.creates_digit_buttons":
        has_createelement = "createelement" in lowered
        if not has_createelement:
            return 0, rule.min_count == 0
        digit_count = sum(1 for d in "0123456789" if f'"{d}"' in lowered or f"'{d}'" in lowered)
        has_decimal = '".". ' in lowered or "'.' " in lowered or '"."' in lowered
        has_equals = '"="' in lowered or "'='" in lowered
        total = digit_count + (1 if has_decimal else 0) + (1 if has_equals else 0)
        return total, total >= 8
    if needle == "creates_operator_buttons" or rule.id == "js.creates_operator_buttons":
        distinct_ops = sum(
            1 for op, alt in [('"+"', "'+'"), ('"-"', "'-'"), ('"*"', "'*'"), ('"/"', "'/'")]
            if any(a in lowered for a in [op, alt])
        )
        return distinct_ops, distinct_ops >= 4
    if needle == "has_updatedisplay" or rule.id == "js.has_updateDisplay":
        has_fn = "updatedisplay" in lowered
        has_value_concat = (
            ("display.value" in lowered and "+=" in lowered) or
            ("thedisplay" in lowered and "+=" in lowered)
        )
        count = 1 if (has_fn or has_value_concat) else 0
        return count, count >= rule.min_count
    if needle == "has_prevalue_preop" or rule.id == "js.has_prevalue_preop_state":
        has_prevalue = "prevalue" in lowered or "prevvalue" in lowered
        has_preop = "preop" in lowered or "prevop" in lowered or "operator" in lowered
        count = sum([has_prevalue, has_preop])
        return count, count >= rule.min_count
    if needle == "has_docalc" or rule.id == "js.has_doCalc":
        has_fn = "docalc" in lowered or "calculate" in lowered or "compute" in lowered
        ops_handled = sum(
            1 for op in ['"+"', '"-"', '"*"', '"/"', "'+'", "'-'", "'*'", "'/'",
                          "case '+'", "case '-'", 'case "+"', 'case "-"']
            if op in lowered
        )
        has_arithmetic = ops_handled >= 2
        count = 1 if (has_fn or has_arithmetic) else 0
        return count, count >= rule.min_count
    if needle == "clears_display" or rule.id == "js.clears_or_updates_display_correctly":
        has_clear = (
            'display.value = ""' in lowered or
            "display.value = ''" in lowered or
            "display.value=''" in lowered or
            'display.value=""' in lowered or
            "thedisplay.value = ''" in lowered
        )
        count = 1 if has_clear else 0
        return count, count >= rule.min_count
    if needle == "uses_createelement" or rule.id == "js.uses_createElement":
        count = lowered.count("createelement(")
        return count, count >= rule.min_count
    if needle == "avoids_document_write" or rule.id == "js.avoids_document_write":
        uses_docwrite = "document.write(" in lowered
        count = 0 if uses_docwrite else 1
        return count, count >= rule.min_count
    if needle == "extra_features" or rule.id == "js.extra_features":
        extras = ["sqrt", "math.sqrt", "percent", "memory", "sin", "cos", "tan", "clear", "clearall", "backspace"]
        count = sum(1 for e in extras if e in lowered)
        return count, count >= rule.min_count
    count = lowered.count(needle)
    return count, count >= rule.min_count


def _evaluate_php_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the php rule."""
    lowered = content.lower()
    needle = rule.needle.lower()
    if needle == "request_superglobal" or rule.id == "php.uses_request":
        patterns = ["$_get", "$_post", "$_request"]
        count = sum(lowered.count(item) for item in patterns)
        return count, count >= rule.min_count
    if needle == "validation" or rule.id == "php.has_validation":
        funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
        count = sum(1 for item in funcs if item in lowered)
        return count, count >= rule.min_count
    if needle == "sanitisation" or rule.id == "php.has_sanitisation":
        funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
        count = sum(1 for item in funcs if item in lowered)
        return count, count >= rule.min_count
    if needle == "output" or rule.id == "php.outputs":
        count = lowered.count("echo") + lowered.count("print")
        return count, count >= rule.min_count
    if needle == "database" or rule.id == "php.uses_database":
        patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
        patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "sessions" or rule.id == "php.uses_sessions":
        patterns = ["session_start", "$_session", "session_destroy"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "loops" or rule.id == "php.has_loops":
        count = sum([
            "for " in lowered or "for(" in lowered,
            "while " in lowered or "while(" in lowered,
            "foreach" in lowered,
        ])
        return count, count >= rule.min_count
    if needle == "error_handling" or rule.id == "php.has_error_handling":
        patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "response_path_complete" or rule.id == "php.response_path_complete":
        has_input = "$_post" in lowered or "$_get" in lowered or "$_request" in lowered
        has_processing = "isset(" in lowered or "if " in lowered or "if(" in lowered
        has_output = "echo" in lowered or "print" in lowered or "json_encode(" in lowered
        count = sum([has_input, has_processing, has_output])
        return count, count >= rule.min_count
    count = lowered.count(needle)
    return count, count >= rule.min_count


def _evaluate_sql_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the sql rule."""
    lowered = content.lower()
    needle = rule.needle.lower()
    if needle == "foreign_key" or rule.id == "sql.has_foreign_key":
        patterns = ["foreign key", "references "]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "constraints" or rule.id == "sql.has_constraints":
        patterns = ["not null", "unique", "check ", "default "]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "data_types" or rule.id == "sql.has_data_types":
        patterns = ["int", "varchar", "text", "date", "datetime", "boolean", "decimal", "float", "char(", "timestamp"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "aggregate" or rule.id == "sql.has_aggregate":
        patterns = ["count(", "sum(", "avg(", "min(", "max(", "group by"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "parses_cleanly" or rule.id == "sql.parses_cleanly":
        has_semicolons = ";" in lowered
        has_statements = "create table" in lowered or "select " in lowered or "insert " in lowered
        open_parens = lowered.count("(")
        close_parens = lowered.count(")")
        parens_balanced = abs(open_parens - close_parens) <= 2
        if not has_semicolons or not has_statements:
            return 0, False
        count = 1 if parens_balanced else 0
        return count, count >= rule.min_count
    count = lowered.count(needle)
    return count, count >= rule.min_count


def _evaluate_api_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Evaluate the api rule."""
    lowered = content.lower()
    needle = rule.needle.lower()
    if needle == "json_encode" or rule.id == "api.json_encode":
        count = lowered.count("json_encode(")
        return count, count >= rule.min_count
    if needle == "application/json" or rule.id == "api.json_content_type":
        count = lowered.count("application/json")
        return count, count >= rule.min_count
    if needle == "request_method" or rule.id == "api.request_method":
        patterns = ['$_server["request_method"]', "$_server['request_method']", "request_method"]
        count = sum(1 for item in patterns if item in lowered)
        return count, count >= rule.min_count
    if needle == "json_decode" or rule.id == "api.json_decode":
        count = lowered.count("json_decode(")
        return count, count >= rule.min_count
    if needle == "fetch" or rule.id == "api.fetch":
        count = lowered.count("fetch(") + lowered.count("fetch (")
        return count, count >= rule.min_count
    if needle == "accepts_method" or rule.id == "api.accepts_method":
        has_request_method = "request_method" in lowered
        has_in_array = "in_array" in lowered and ("'get'" in lowered or "'post'" in lowered)
        count = 1 if (has_request_method or has_in_array) else 0
        return count, count >= rule.min_count
    if needle == "valid_json_shape" or rule.id == "api.valid_json_shape":
        has_json_encode = "json_encode(" in lowered
        has_array_arg = (
            "json_encode([" in lowered or
            "json_encode(array(" in lowered or
            "json_encode(['" in lowered or
            'json_encode(["' in lowered
        )
        count = 1 if (has_json_encode and has_array_arg) else 0
        return count, count >= rule.min_count
    if needle == "http_status_codes" or rule.id == "api.http_status_codes":
        has_response_code = "http_response_code(" in lowered
        has_header_http = 'header("http/' in lowered or "header('http/" in lowered
        count = 1 if (has_response_code or has_header_http) else 0
        return count, count >= rule.min_count
    if needle == "error_response_path" or rule.id == "api.error_response_path":
        has_json_encode = "json_encode(" in lowered
        has_error_key = "'error'" in lowered or '"error"' in lowered or "'message'" in lowered or '"message"' in lowered
        has_condition = "if " in lowered or "if(" in lowered or "catch" in lowered
        count = 1 if (has_json_encode and has_error_key and has_condition) else 0
        return count, count >= rule.min_count
    count = lowered.count(needle)
    return count, count >= rule.min_count


def evaluate_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    """Dispatch to the appropriate component rule evaluator."""
    dispatch = {
        "html": _evaluate_html_rule,
        "css": _evaluate_css_rule,
        "js": _evaluate_js_rule,
        "php": _evaluate_php_rule,
        "sql": _evaluate_sql_rule,
        "api": _evaluate_api_rule,
    }
    return dispatch[component](component, rule, content)
