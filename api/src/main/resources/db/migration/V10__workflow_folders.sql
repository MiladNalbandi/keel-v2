-- v0.9.0: folders on the Workflows page. Any workflow can sit in one, keel's templates too, per project.
CREATE TABLE workflow_folders (
    project_id  TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    folder      TEXT NOT NULL,
    PRIMARY KEY (project_id, workflow_id)
);
