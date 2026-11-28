# CP-2: Add Breed and Weight fields to the Pet entity

Source: [CP-2 in Jira](https://capstoneprojectgrp4.atlassian.net/browse/CP-2)

## Description

Implementation Details:

1. Update the Pet entity/model class to include new properties: `breed` (string) and `weight` (decimal/float).
2. Generate and run database migration scripts to add `breed` and `weight` columns to the pets table.
3. Update Pet creation and update forms/DTOs to collect and save the new fields.
4. Update Pet profile views/API responses to display breed and weight.
5. Add unit and integration tests covering the updated Pet entity persistence and validation.
