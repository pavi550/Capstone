# MCP GitHub & Jira SDLC Workflow

## Introduction

This document outlines the Software Development Life Cycle (SDLC) workflow for the MCP project, focusing on the integration of GitHub and Jira for project management and issue tracking.

## GitHub Workflow

### 1. Issue Tracking

- **Jira**: All project issues, bugs, and feature requests are tracked in Jira. Issues are created, assigned, and prioritized by the development team.
- **GitHub**: GitHub is used for code repositories, pull requests, and code reviews. Issues related to code changes are linked to Jira issues.

### 2. Code Development

- **Branching Strategy**: A feature branch is created from the `main` branch for each new feature or bug fix.
- **Code Committing**: Developers commit code changes to their feature branches.
- **Code Review**: Pull requests are raised from feature branches to the `main` branch. Code reviews are conducted by the team to ensure code quality and adherence to coding standards.
- **Merge Strategy**: Once approved, the pull request is merged into the `main` branch.

## Jira Workflow

### 1. Issue Creation

- **Jira**: Issues are created by the development team based on user stories, bugs, or feature requests.
- **GitHub**: The issue is linked to a corresponding GitHub repository and pull request.

### 2. Development and Testing

- **GitHub**: Developers work on their feature branches and raise pull requests for code review.
- **Jira**: The development team updates the status of the issue in Jira as the code is developed and tested.

### 3. Code Review and Testing

- **GitHub**: Pull requests are reviewed by the team to ensure code quality and functionality.
- **Jira**: The development team updates the issue status in Jira as the code is reviewed and tested.

### 4. Deployment

- **GitHub**: Once the pull request is approved, the code is merged into the `main` branch.
- **Jira**: The issue is marked as resolved and closed in Jira.

## Conclusion

The integration of GitHub and Jira ensures a seamless and efficient SDLC workflow, promoting collaboration, code quality, and timely delivery of project milestones.