### Pattern Description
In Presto, a join operation involves two tables: the probe side (the first table being probed) and the build side (the second table being built). The build side is used to create a hash table that is used to match rows from the probe side. When the build side is too large, it can cause the query to run out of memory or become very slow. This is due to Presto having to materialize the build side in memory before it can start processing the probe side.
See [Tips on Joins](https://fburl.com/wiki/kop3q8nb) for more details.

### How is this Pattern Detected?
Dr. Presto identifies queries where the build side is larger and displays this in the query page:
![](/intern/wiki/_download/?title=7fca09ff-090e-4603-a3ff-9e14d6c39e4eimage.png)
The general structure of the warning is to give the two sets of tables `({probe_table1,...}, {build_table1,...})` along with the size of each side of the join.
We filter this further by requiring the ratio of build / probe side > 5.

### **EXAMPLES**
[D71918181](/intern/diff/71918181/) saved ~10K BCU, 85% BCU reduction.

### WHAT TO DO?
Flip the join order of the join so that the probe (left) side is the larger side. The details are described below for the simple case where only two tables are involved and the multiple table case where the switch involves multiple tables on either side.

### Simple Case
For the case where only a **single table is listed in both sets** given in the warning (i.e.  ({probe_table1},{build_table1}) you would do the following.
**Inner Joins**
For inner joins a simple swap should work so that this:
```
SELECT
    *
FROM small_table s
JOIN large_table l
```
should be rewritten to:
```
SELECT
    *
FROM large_table b
JOIN small_table a
```
**LEFT/RIGHT joins**
For any LEFT or RIGHT joins you'd have to swap the direction of the join so that this:
```
SELECT
    *
FROM small_table s
LEFT JOIN large_table l
```
should be rewritten to:
```
SELECT
    *
FROM large_table l
RIGHT JOIN small_table s
```

### Multiple Tables Case
For the case where **multiple tables** are listed in either set given the warning `({probe_table1, prrobe_table2,...}, {build_table1, build_table2,...})` you would do the following.
1. Treat each set as a single unit, swap the two units
2. If the join type is LEFT/RIGHT join, swap the join type like described above
3. Make sure to move each join condition to their appropriate place for each join
So you might be given the table sets `({tableA, tableB}, {tableC, tableD})` for the following query:
```
SELECT
    ...
FROM tableA A
JOIN tableB B
(join condition)
LEFT JOIN tableC C
(join condition)
RIGHT JOIN tableD D
(join condition)
```
Following the steps above we note:
1. We need to swap the tables (tableA, tableB) with the tables (tableC, tableD)
2. The original join with tableC was a LEFT join, so we'll switch it to a RIGHT join
3. We make sure each join condition is re-arranged to the right place
The final query becomes:
```
SELECT
    ...
FROM tableC C
RIGHT JOIN tableD D
(join condition)
RIGHT JOIN tableA A
(join condition)
JOIN tableB B
(join condition)
```
