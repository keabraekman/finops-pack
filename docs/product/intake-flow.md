# Intake Flow

AWS Savings Review uses one short intake flow:

1. The prospect reviews the setup instructions.
2. They choose whether the assessment is for one AWS account or an AWS Organization.
3. They submit company name, contact name, work email, role ARN, external ID, and prerequisite confirmations.
4. The API validates the role ARN, external ID, assume-role access, and billing prerequisites.
5. The API creates a lead, creates an assessment run, and enqueues a background job.
6. The worker generates the dashboard and appendix.
7. The status page shows progress and then the in-browser report.

The website never asks for AWS passwords, console login access, or long-term access keys.

