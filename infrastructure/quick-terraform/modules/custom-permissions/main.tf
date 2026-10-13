# Module: custom-permissions
# Resolves each profile to a concrete set of DENY capabilities from (in precedence order):
#   raw capabilities override > allow-list exceptions > preset > deny_by_default base.
# Then provisions aws_quicksight_custom_permissions (native) or a CloudFormation stack
# (fallback), plus role bindings.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  native = !var.use_cloudformation

  # The full capability surface supported by the provider.
  all_capabilities = [
    "add_or_run_anomaly_detection_for_analyses",
    "create_and_update_dashboard_email_reports",
    "create_and_update_data_sources",
    "create_and_update_datasets",
    "create_and_update_themes",
    "create_and_update_threshold_alerts",
    "create_shared_folders",
    "create_spice_dataset",
    "export_to_csv",
    "export_to_excel",
    "export_to_pdf",
    "include_content_in_scheduled_reports_email",
    "print_reports",
    "rename_shared_folders",
    "share_analyses",
    "share_dashboards",
    "share_data_sources",
    "share_datasets",
    "subscribe_dashboard_email_reports",
    "view_account_spice_capacity",
  ]

  # Named preset postures: each lists the capabilities to DENY.
  presets = {
    # Readers: deny all authoring, sharing, data-source, and dataset creation.
    reader_lockdown = [
      "add_or_run_anomaly_detection_for_analyses",
      "create_and_update_data_sources",
      "create_and_update_datasets",
      "create_and_update_themes",
      "create_and_update_threshold_alerts",
      "create_shared_folders",
      "create_spice_dataset",
      "rename_shared_folders",
      "share_analyses",
      "share_dashboards",
      "share_data_sources",
      "share_datasets",
    ]
    # Stop data leaving: deny every export path + sharing data sources/datasets.
    no_data_exfiltration = [
      "export_to_csv",
      "export_to_excel",
      "export_to_pdf",
      "print_reports",
      "share_data_sources",
      "share_datasets",
      "include_content_in_scheduled_reports_email",
    ]
    # Authors who may build but not share externally.
    author_no_share = [
      "share_analyses",
      "share_dashboards",
      "share_data_sources",
      "share_datasets",
      "create_shared_folders",
      "rename_shared_folders",
    ]
    # Pro role, hardened: no sharing, no export.
    strict_pro = [
      "share_analyses",
      "share_dashboards",
      "share_data_sources",
      "share_datasets",
      "export_to_csv",
      "export_to_excel",
      "export_to_pdf",
    ]
  }

  # Resolve each profile to its final DENY set.
  resolved = {
    for name, p in var.profiles : name => toset(compact(concat(
      # Base: deny everything when deny_by_default, else nothing.
      p.deny_by_default ? local.all_capabilities : [],
      # Preset posture (if named).
      p.preset != null ? lookup(local.presets, p.preset, []) : [],
      # Raw explicit DENYs.
      [for cap, v in p.capabilities : cap if upper(v) == "DENY"],
    )))
  }

  # Apply allow-list: remove explicitly-allowed capabilities from the DENY set,
  # UNLESS they were explicitly set to DENY via the raw capabilities map (raw wins).
  final_deny = {
    for name, denies in local.resolved : name => toset([
      for cap in denies : cap
      if !contains(var.profiles[name].allow, cap) || upper(lookup(var.profiles[name].capabilities, cap, "")) == "DENY"
    ])
  }
}

# -----------------------------------------------------------------------------
# Native Terraform path
# -----------------------------------------------------------------------------
resource "aws_quicksight_custom_permissions" "this" {
  for_each = local.native ? var.profiles : {}

  aws_account_id          = data.aws_caller_identity.current.account_id
  custom_permissions_name = each.key

  capabilities {
    add_or_run_anomaly_detection_for_analyses  = contains(local.final_deny[each.key], "add_or_run_anomaly_detection_for_analyses") ? "DENY" : null
    create_and_update_dashboard_email_reports  = contains(local.final_deny[each.key], "create_and_update_dashboard_email_reports") ? "DENY" : null
    create_and_update_data_sources             = contains(local.final_deny[each.key], "create_and_update_data_sources") ? "DENY" : null
    create_and_update_datasets                 = contains(local.final_deny[each.key], "create_and_update_datasets") ? "DENY" : null
    create_and_update_themes                   = contains(local.final_deny[each.key], "create_and_update_themes") ? "DENY" : null
    create_and_update_threshold_alerts         = contains(local.final_deny[each.key], "create_and_update_threshold_alerts") ? "DENY" : null
    create_shared_folders                      = contains(local.final_deny[each.key], "create_shared_folders") ? "DENY" : null
    create_spice_dataset                       = contains(local.final_deny[each.key], "create_spice_dataset") ? "DENY" : null
    export_to_csv                              = contains(local.final_deny[each.key], "export_to_csv") ? "DENY" : null
    export_to_excel                            = contains(local.final_deny[each.key], "export_to_excel") ? "DENY" : null
    export_to_pdf                              = contains(local.final_deny[each.key], "export_to_pdf") ? "DENY" : null
    include_content_in_scheduled_reports_email = contains(local.final_deny[each.key], "include_content_in_scheduled_reports_email") ? "DENY" : null
    print_reports                              = contains(local.final_deny[each.key], "print_reports") ? "DENY" : null
    rename_shared_folders                      = contains(local.final_deny[each.key], "rename_shared_folders") ? "DENY" : null
    share_analyses                             = contains(local.final_deny[each.key], "share_analyses") ? "DENY" : null
    share_dashboards                           = contains(local.final_deny[each.key], "share_dashboards") ? "DENY" : null
    share_data_sources                         = contains(local.final_deny[each.key], "share_data_sources") ? "DENY" : null
    share_datasets                             = contains(local.final_deny[each.key], "share_datasets") ? "DENY" : null
    subscribe_dashboard_email_reports          = contains(local.final_deny[each.key], "subscribe_dashboard_email_reports") ? "DENY" : null
    view_account_spice_capacity                = contains(local.final_deny[each.key], "view_account_spice_capacity") ? "DENY" : null
  }

  tags = var.tags
}

resource "aws_quicksight_role_custom_permission" "this" {
  for_each = local.native ? var.role_assignments : {}

  aws_account_id          = data.aws_caller_identity.current.account_id
  namespace               = var.namespace
  role                    = each.key
  custom_permissions_name = each.value

  depends_on = [aws_quicksight_custom_permissions.this]
}

# -----------------------------------------------------------------------------
# CloudFormation fallback path (AWS::QuickSight::CustomPermissions)
# -----------------------------------------------------------------------------
resource "aws_cloudformation_stack" "custom_permissions" {
  for_each = local.native ? {} : var.profiles

  name = "amazon-quick-custompermissions-${each.key}"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "Amazon Quick custom-permission profile ${each.key}"
    Resources = {
      Profile = {
        Type = "AWS::QuickSight::CustomPermissions"
        Properties = {
          AwsAccountId          = data.aws_caller_identity.current.account_id
          CustomPermissionsName = each.key
          Capabilities          = { for cap in local.final_deny[each.key] : cap => "DENY" }
        }
      }
    }
    Outputs = {
      Arn = { Value = { "Fn::GetAtt" = ["Profile", "Arn"] } }
    }
  })

  capabilities = ["CAPABILITY_NAMED_IAM"]
  tags         = var.tags
}
