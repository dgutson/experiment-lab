# View files

A query asked again and again goes in a view file of the project, a file of `CREATE VIEW`
statements over the views of the skill's table:

```sql
-- Each finished run of "rate test", with its accuracy.
CREATE VIEW accuracy AS SELECT name, metrics['accuracy'] AS accuracy
    FROM runs WHERE experiment = 'rate test' AND status = 'FINISHED';
```

`rec project NAME --views FILE ...` sets the project's view files, every one in order. q and the
owner's pages run them; a Python script does not.
