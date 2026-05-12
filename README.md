# Ansible XML Validation Agent

This repository contains an Ansible role that renders an XML application configuration, validates it with a custom Python Ansible module, writes JSON and Markdown reports, and fails CI when validation errors are found.

When enabled, the custom module can also call an AI API for repair suggestions. API credentials are read only from an environment variable and are never written to reports.

## Repository Flow

1. `playbook/site.yml` runs locally and loads the `xml_validation_agent` role.
2. The role loads demo application values from `roles/xml_validation_agent/vars/sample_config.yml`.
3. `templates/app_config.xml.j2` renders the XML file to `build/app_config.xml`.
4. `library/xml_validate_ai.py` validates the XML for:
   - file existence
   - XML well-formedness
   - XSD compliance
   - required tag presence
   - empty required elements
5. The module writes:
   - `reports/xml_validation_report.json`
   - `reports/xml_validation_report.md`
6. The play fails when validation errors exist.
7. GitHub Actions uploads `reports/` as the `xml-validation-reports` artifact, even if validation fails.

## Run Locally

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the playbook:

```bash
ansible-playbook -i inventory/localhost.ini playbook/site.yml
```

The successful run creates `build/app_config.xml` and validation reports under `reports/`.

## Enable AI Repair Suggestions

AI calls are disabled by default:

```yaml
ai_enabled: false
```

To enable them, set role variables such as:

```yaml
ai_enabled: true
ai_api_url: "https://api.example.com/xml-repair"
ai_api_key_env_var: XML_AI_API_KEY
```

For local testing, export the API key before running Ansible:

```bash
export XML_AI_API_KEY="your-api-key"
```

On Windows PowerShell:

```powershell
$env:XML_AI_API_KEY = "your-api-key"
```

## Configure GitHub Actions Secret

In GitHub:

1. Open the repository settings.
2. Go to **Secrets and variables** > **Actions**.
3. Create a repository secret named `XML_AI_API_KEY`.
4. Enable AI only when you have also configured `ai_api_url`.

The default workflow does not call an AI API because `ai_enabled` is `false`.

## Intentionally Test a Failure

To test CI failure and report generation, break the template or sample values. For example, change the database port in `roles/xml_validation_agent/vars/sample_config.yml` to a non-integer value:

```yaml
db_port: not-a-number
```

Then run:

```bash
ansible-playbook -i inventory/localhost.ini playbook/site.yml
```

The playbook will still write JSON and Markdown reports before failing.

## Reports

The JSON report is structured for automation:

```json
{
  "status": "failed",
  "summary": {
    "validation_errors_count": 1,
    "validation_warnings_count": 0,
    "ai_enabled": false,
    "reports_written": true
  },
  "validation_errors": [
    {
      "code": "schema_validation_failed",
      "message": "Example validation failure"
    }
  ]
}
```

The Markdown report is designed for quick review in CI artifacts and includes summary counts, validation errors, validation warnings, and AI suggestions when available.

## GitHub Actions Artifact

The workflow uploads the entire `reports/` directory as:

```text
xml-validation-reports
```

Expected artifact contents:

```text
reports/
├── xml_validation_report.json
└── xml_validation_report.md
```

## Important Defaults

Key defaults live in `roles/xml_validation_agent/defaults/main.yml`:

```yaml
xml_output_path: "{{ playbook_dir }}/../build/app_config.xml"
xml_schema_path: "{{ role_path }}/files/app_config.xsd"
xml_report_json_path: "{{ playbook_dir }}/../reports/xml_validation_report.json"
xml_report_md_path: "{{ playbook_dir }}/../reports/xml_validation_report.md"
ai_enabled: false
ai_api_key_env_var: XML_AI_API_KEY
```

No API keys are hardcoded. The module calls the AI API only when `ai_enabled` is true and validation errors exist.
