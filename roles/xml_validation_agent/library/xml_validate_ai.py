#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: xml_validate_ai
short_description: Validate XML against XSD and optionally request AI repair suggestions
description:
  - Validates an XML document for existence, well-formedness, XSD compliance, required XPath presence, and empty required elements.
  - Writes JSON and Markdown reports before returning.
  - Optionally calls an AI API for repair suggestions when validation errors are found.
options:
  xml_path:
    description: Path to the XML file to validate.
    type: str
    required: true
  schema_path:
    description: Path to the XSD schema file.
    type: str
    required: true
  required_tags:
    description: XPath expressions that must exist and contain non-empty text.
    type: list
    elements: str
    default: []
  report_json_path:
    description: Path where the JSON report will be written.
    type: str
    required: true
  report_md_path:
    description: Path where the Markdown report will be written.
    type: str
    required: true
  ai_enabled:
    description: Whether to call the AI API when validation errors are found.
    type: bool
    default: false
  ai_api_url:
    description: AI API endpoint URL.
    type: str
    required: false
  ai_api_key_env_var:
    description: Environment variable name containing the AI API key.
    type: str
    default: XML_AI_API_KEY
  ai_model:
    description: Model name to send to an OpenAI-compatible chat completions API.
    type: str
    default: azure.gpt-4o-mini
supports_check_mode: true
author:
  - XML Validation Agent
"""

EXAMPLES = r"""
- name: Validate rendered XML
  xml_validate_ai:
    xml_path: /tmp/app_config.xml
    schema_path: /tmp/app_config.xsd
    required_tags:
      - /application/name
    report_json_path: /tmp/xml_validation_report.json
    report_md_path: /tmp/xml_validation_report.md
"""

RETURN = r"""
validation_errors:
  description: Validation errors found in the XML document.
  returned: always
  type: list
  elements: dict
validation_warnings:
  description: Non-fatal validation warnings.
  returned: always
  type: list
  elements: dict
validation_errors_count:
  description: Number of validation errors.
  returned: always
  type: int
validation_warnings_count:
  description: Number of validation warnings.
  returned: always
  type: int
report:
  description: Complete report payload.
  returned: always
  type: dict
"""

import datetime
import json
import os
import traceback

from ansible.module_utils.basic import AnsibleModule

try:
    import requests
except ImportError:
    requests = None

try:
    from lxml import etree
except ImportError:
    etree = None


def utc_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def make_issue(code, message, path=None, details=None):
    issue = {
        "code": code,
        "message": message,
    }
    if path:
        issue["path"] = path
    if details is not None:
        issue["details"] = details
    return issue


def ensure_parent_directory(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_json_report(path, report):
    ensure_parent_directory(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def markdown_escape(value):
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def issue_table(title, issues):
    lines = ["## {0}".format(title), ""]
    if not issues:
        lines.extend(["None.", ""])
        return lines

    lines.extend(["| Code | Path | Message |", "| --- | --- | --- |"])
    for issue in issues:
        lines.append(
            "| {code} | {path} | {message} |".format(
                code=markdown_escape(issue.get("code", "")),
                path=markdown_escape(issue.get("path", "")),
                message=markdown_escape(issue.get("message", "")),
            )
        )
    lines.append("")
    return lines


def write_markdown_report(path, report):
    ensure_parent_directory(path)

    lines = [
        "# XML Validation Report",
        "",
        "- Generated at: `{0}`".format(report["generated_at"]),
        "- XML path: `{0}`".format(report["xml_path"]),
        "- Schema path: `{0}`".format(report["schema_path"]),
        "- Validation status: **{0}**".format(report["status"]),
        "- Validation errors: **{0}**".format(report["summary"]["validation_errors_count"]),
        "- Validation warnings: **{0}**".format(report["summary"]["validation_warnings_count"]),
        "",
    ]
    lines.extend(issue_table("Validation Errors", report["validation_errors"]))
    lines.extend(issue_table("Validation Warnings", report["validation_warnings"]))

    lines.extend(["## AI Suggestions", ""])
    ai_suggestions = report.get("ai_suggestions")
    if ai_suggestions:
        if isinstance(ai_suggestions, (dict, list)):
            lines.extend(["```json", json.dumps(ai_suggestions, indent=2, sort_keys=True), "```", ""])
        else:
            lines.extend([str(ai_suggestions), ""])
    else:
        lines.extend(["None.", ""])

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def parse_xml(xml_path, validation_errors):
    if etree is None:
        validation_errors.append(
            make_issue("dependency_missing", "Python package lxml is required but is not installed.")
        )
        return None

    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
    try:
        return etree.parse(xml_path, parser)
    except etree.XMLSyntaxError as exc:
        validation_errors.append(
            make_issue(
                "xml_not_well_formed",
                "XML is not well formed.",
                details=str(exc),
            )
        )
    except OSError as exc:
        validation_errors.append(
            make_issue(
                "xml_read_error",
                "Unable to read XML file.",
                path=xml_path,
                details=str(exc),
            )
        )
    return None


def validate_schema(xml_tree, schema_path, validation_errors):
    if xml_tree is None or etree is None:
        return

    if not os.path.exists(schema_path):
        validation_errors.append(
            make_issue("schema_missing", "Schema file does not exist.", path=schema_path)
        )
        return

    try:
        schema_doc = etree.parse(schema_path)
        schema = etree.XMLSchema(schema_doc)
    except etree.XMLSchemaParseError as exc:
        validation_errors.append(
            make_issue(
                "schema_invalid",
                "XSD schema is not valid.",
                path=schema_path,
                details=str(exc),
            )
        )
        return
    except (etree.XMLSyntaxError, OSError) as exc:
        validation_errors.append(
            make_issue(
                "schema_read_error",
                "Unable to read or parse XSD schema.",
                path=schema_path,
                details=str(exc),
            )
        )
        return

    if not schema.validate(xml_tree):
        for error in schema.error_log:
            validation_errors.append(
                make_issue(
                    "schema_validation_failed",
                    error.message,
                    path="line {0}".format(error.line),
                    details={
                        "domain": error.domain_name,
                        "level": error.level_name,
                        "type": error.type_name,
                    },
                )
            )


def validate_required_tags(xml_tree, required_tags, validation_errors):
    if xml_tree is None:
        return

    for tag_path in required_tags:
        try:
            matches = xml_tree.xpath(tag_path)
        except etree.XPathError as exc:
            validation_errors.append(
                make_issue(
                    "required_tag_xpath_invalid",
                    "Required tag XPath is invalid.",
                    path=tag_path,
                    details=str(exc),
                )
            )
            continue

        if not matches:
            validation_errors.append(
                make_issue(
                    "required_tag_missing",
                    "Required XML tag path was not found.",
                    path=tag_path,
                )
            )
            continue

        for element in matches:
            if isinstance(element, etree._Element):
                text_content = "".join(element.itertext()).strip()
                if not text_content:
                    validation_errors.append(
                        make_issue(
                            "required_tag_empty",
                            "Required XML tag exists but is empty.",
                            path=tag_path,
                        )
                    )
            elif element is None or str(element).strip() == "":
                validation_errors.append(
                    make_issue(
                        "required_tag_empty",
                        "Required XML value exists but is empty.",
                        path=tag_path,
                    )
                )


def build_ai_endpoint(ai_api_url):
    cleaned_url = ai_api_url.rstrip("/")
    if cleaned_url.endswith("/chat/completions") or cleaned_url.endswith("/responses"):
        return cleaned_url
    return "{0}/openai/v1/chat/completions".format(cleaned_url)


def build_ai_prompt(xml_content, validation_errors):
    return (
        "Review this rendered XML configuration and its validation errors. "
        "Return concise repair suggestions with exact fields or XML elements to change.\n\n"
        "Validation errors:\n{0}\n\n"
        "XML content:\n{1}"
    ).format(json.dumps(validation_errors, indent=2, sort_keys=True), xml_content)


def extract_ai_suggestions(response):
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response.text

    payload = response.json()
    if not isinstance(payload, dict):
        return payload

    choices = payload.get("choices")
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if content:
            return content

    output = payload.get("output")
    if output:
        return output

    return payload


def call_ai_api(ai_api_url, api_key, ai_model, xml_content, validation_errors):
    if requests is None:
        raise RuntimeError("Python package requests is required for AI suggestions but is not installed.")

    endpoint = build_ai_endpoint(ai_api_url)
    headers = {
        "Authorization": "Bearer {0}".format(api_key),
        "Content-Type": "application/json",
    }
    payload = {
        "model": ai_model,
        "messages": [
            {
                "role": "system",
                "content": "You are an Ansible and XML configuration validation assistant.",
            },
            {
                "role": "user",
                "content": build_ai_prompt(xml_content, validation_errors),
            },
        ],
        "temperature": 0.1,
    }
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return extract_ai_suggestions(response)


def maybe_get_ai_suggestions(
    ai_enabled,
    ai_api_url,
    ai_api_key_env_var,
    ai_model,
    xml_content,
    validation_errors,
    validation_warnings,
):
    if not ai_enabled or not validation_errors:
        return None

    if not ai_api_url:
        validation_warnings.append(
            make_issue(
                "ai_api_url_missing",
                "AI suggestions were enabled, but no ai_api_url was provided.",
            )
        )
        return None

    api_key = os.environ.get(ai_api_key_env_var)
    if not api_key:
        validation_warnings.append(
            make_issue(
                "ai_api_key_missing",
                "AI suggestions were enabled, but the configured API key environment variable is not set.",
                path=ai_api_key_env_var,
            )
        )
        return None

    try:
        return call_ai_api(ai_api_url, api_key, ai_model, xml_content, validation_errors)
    except Exception as exc:
        validation_warnings.append(
            make_issue(
                "ai_request_failed",
                "AI suggestion request failed.",
                details=str(exc),
            )
        )
        return None


def build_report(params, validation_errors, validation_warnings, ai_suggestions, skipped_write=False):
    return {
        "generated_at": utc_timestamp(),
        "xml_path": params["xml_path"],
        "schema_path": params["schema_path"],
        "status": "failed" if validation_errors else "passed",
        "summary": {
            "validation_errors_count": len(validation_errors),
            "validation_warnings_count": len(validation_warnings),
            "ai_enabled": params["ai_enabled"],
            "reports_written": not skipped_write,
        },
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "ai_suggestions": ai_suggestions,
    }


def run_module():
    module_args = {
        "xml_path": {"type": "str", "required": True},
        "schema_path": {"type": "str", "required": True},
        "required_tags": {"type": "list", "elements": "str", "default": []},
        "report_json_path": {"type": "str", "required": True},
        "report_md_path": {"type": "str", "required": True},
        "ai_enabled": {"type": "bool", "default": False},
        "ai_api_url": {"type": "str", "required": False, "default": ""},
        "ai_api_key_env_var": {"type": "str", "required": False, "default": "XML_AI_API_KEY"},
        "ai_model": {"type": "str", "required": False, "default": "azure.gpt-4o-mini"},
    }

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    params = module.params

    validation_errors = []
    validation_warnings = []
    ai_suggestions = None
    xml_content = ""

    xml_path = params["xml_path"]
    schema_path = params["schema_path"]
    report_json_path = params["report_json_path"]
    report_md_path = params["report_md_path"]

    if not os.path.exists(xml_path):
        validation_errors.append(
            make_issue("xml_missing", "XML file does not exist.", path=xml_path)
        )
    else:
        try:
            xml_content = read_text(xml_path)
        except OSError as exc:
            validation_errors.append(
                make_issue(
                    "xml_read_error",
                    "Unable to read XML file.",
                    path=xml_path,
                    details=str(exc),
                )
            )

    xml_tree = None
    if os.path.exists(xml_path):
        xml_tree = parse_xml(xml_path, validation_errors)
        validate_schema(xml_tree, schema_path, validation_errors)
        validate_required_tags(xml_tree, params["required_tags"], validation_errors)

    ai_suggestions = maybe_get_ai_suggestions(
        params["ai_enabled"],
        params["ai_api_url"],
        params["ai_api_key_env_var"],
        params["ai_model"],
        xml_content,
        validation_errors,
        validation_warnings,
    )

    if module.check_mode:
        report = build_report(
            params,
            validation_errors,
            validation_warnings,
            ai_suggestions,
            skipped_write=True,
        )
        module.exit_json(
            changed=False,
            failed=bool(validation_errors),
            msg="XML validation completed in check mode; reports were not written.",
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
            validation_errors_count=len(validation_errors),
            validation_warnings_count=len(validation_warnings),
            report=report,
        )

    report = build_report(params, validation_errors, validation_warnings, ai_suggestions)

    try:
        write_json_report(report_json_path, report)
        write_markdown_report(report_md_path, report)
    except Exception as exc:
        module.fail_json(
            msg="Failed to write validation reports.",
            exception=traceback.format_exc(),
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
            report_write_error=str(exc),
        )

    result = {
        "changed": True,
        "failed": bool(validation_errors),
        "msg": "XML validation failed." if validation_errors else "XML validation passed.",
        "validation_errors": validation_errors,
        "validation_warnings": validation_warnings,
        "validation_errors_count": len(validation_errors),
        "validation_warnings_count": len(validation_warnings),
        "report_json_path": report_json_path,
        "report_md_path": report_md_path,
        "report": report,
    }

    if validation_errors:
        module.fail_json(**result)
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
