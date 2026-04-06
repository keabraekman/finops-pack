# Account Scope

The intake form asks:

> Is this one AWS account or an AWS Organization?

## One AWS Account

The customer submits one read-only role ARN and external ID from the account to review. The worker runs the assessment against that role and scans active commercial AWS regions when permissions allow region discovery.

## AWS Organization

The customer submits one management-account role ARN and external ID. The worker first attempts AWS Organizations discovery through that role, stores discovered account metadata on the run, and then continues with the existing report-generation path.

If Organizations discovery is unavailable, the run continues against the submitted management-account role and records the discovery limitation as coverage context.

