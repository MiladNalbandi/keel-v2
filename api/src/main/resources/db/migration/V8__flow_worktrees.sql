-- v0.7.x: several flows at once. A flow next to another one runs in its own git worktree under
-- <project>/.keel/worktrees/<worktree> on its own branch; NULL = the project folder.
ALTER TABLE threads ADD COLUMN worktree TEXT;
ALTER TABLE threads ADD COLUMN branch TEXT;
