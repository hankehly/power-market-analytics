# Kimball Dimensional Modeling Techniques

## Fundamental Concepts

### Gather Business Requirements and Data Realities

Before launching a dimensional modeling effort, understand both the needs of the business and
the realities of the underlying source data. Uncover requirements in sessions with business
representatives, covering their objectives, KPIs, compelling business issues, decision-making
processes, and analytic needs. In parallel, meet with source system experts and profile the data
at a high level to assess feasibility.

### Collaborative Dimensional Modeling Workshops

Design dimensional models in collaboration with subject matter experts and data governance
representatives from the business, in a series of interactive workshops led by the data modeler.
Never design in isolation by people who don't fully understand the business and its needs.

### Four-Step Dimensional Design Process

Four key decisions drive the design of a dimensional model: select the business process, declare
the grain, identify the dimensions, and identify the facts. Business needs and source-data
realities, surfaced in the collaborative sessions above, answer these questions; the design team
then settles table and column names, sample domain values, and business rules, with data
governance representatives present to secure business buy-in.

### Business Processes

_Business processes_ are the operational activities an organization performs — taking an order,
processing an insurance claim, registering students for a class, snapshotting an account each
month. They generate performance metrics that become fact-table facts. Most fact tables focus on
a single business process; choosing it defines the design target and lets grain, dimensions, and
facts be declared. Each business process is a row in the enterprise data warehouse bus matrix.

### Grain

Declaring the grain — exactly what a single fact table row represents — is the pivotal, binding step
in a dimensional design, made before choosing dimensions or facts so every candidate stays
consistent with it. Start with atomic-grained data: it withstands unpredictable user queries, while
rolled-up summary grains help performance but presuppose the business's common questions.
Different grains require separate physical fact tables.

### Dimensions for Descriptive Context

Dimensions supply the who/what/where/when/why/how context around a business process event,
holding the descriptive attributes BI applications use to filter and group facts. A dimension should
be single-valued per fact row wherever possible. Dimension tables are called the "soul" of the
warehouse because their entry points and descriptive labels drive the BI experience, which is why
disproportionate governance and development effort goes into them.

### Facts for Measurements

_Facts_ are the numeric measurements from a business process event, with a one-to-one
relationship to a measurement event at the fact table's grain — the table reflects a physical
observable event, not a particular report's demands. Only facts consistent with the declared grain
belong in the table (a retail sale's quantity and extended price qualify; the store manager's salary
does not).

### Star Schemas and OLAP cubes

_Star schemas_ are dimensional structures in an RDBMS: fact tables linked to dimension tables via
primary/foreign keys. An _OLAP cube_ is the equivalent structure in a multidimensional database,
usually derived from a relational star schema and accessed through richer analytic languages (e.g.
XMLA) than SQL. A cube is often the final deployment step of a dimensional DW/BI system, or an
aggregate structure atop a more atomic star schema.

### Grace Extensions to Dimensional Modeling

Dimensional models absorb these changes without altering any existing BI query, application, or
result: adding facts consistent with an existing fact table's grain as new columns; adding
dimensions to a fact table as new foreign keys, provided the grain is unchanged; adding attributes
to an existing dimension as new columns; and restating a fact table at a lower grain by adding
attributes to a dimension, while preserving existing column names.

## Basic Fact Table Techniques

### Fact Table Structure

A _fact table_ holds the numeric measures from an operational measurement event; at the lowest
grain, one row corresponds to one event and vice versa, so the design follows the real-world
activity rather than eventual reports. Besides measures, a fact table carries a foreign key per
associated dimension, plus optional degenerate dimension keys and date/time stamps. It is the
primary target of query computation and dynamic aggregation.

### Additive, Semi-Additive, and Non-Additive Facts

_Additive_ measures — the most useful — sum across any dimension of the fact table.
_Semi-additive_ measures sum across some dimensions but not all (balance amounts are additive
across everything except time). _Non-additive_ measures, such as ratios, are not summable at all;
where possible, store the fully additive components of a non-additive measure and compute the
final ratio after summing the components, often in the BI layer or OLAP cube.

### Nulls in Fact Tables

Null measurements behave gracefully — SUM, COUNT, MIN, MAX, and AVG all do the right thing
with them. Nulls must, however, be avoided in a fact table's foreign keys, since they would violate
referential integrity; give the dimension a default row and surrogate key for the unknown/not
applicable condition instead.

### Conformed Facts

When the same measurement appears in separate fact tables, its technical definition must be
identical for it to be compared or combined across them. Give consistent facts the same name;
give inconsistent ones different names so business users and BI applications aren't misled.

### Transaction Fact Tables

A row in a _transaction fact table_ is a measurement event at a point in space and time. Being the
most dimensional and expressive kind, these tables enable maximum slicing and dicing, and may
be dense or sparse since rows exist only when a measurement occurs. They carry a foreign key
per dimension, and optionally precise timestamps and degenerate dimensions, with facts
consistent with the transaction grain.

### Periodic Snapshot Fact Tables

A row in a _periodic snapshot fact table_ summarizes many measurement events over a standard
period (day, week, month); the grain is the period, not the individual transaction. These tables
often carry many facts — any measurement consistent with the grain is permissible — and are
uniformly dense in their foreign keys, typically inserting a zero or null row even when no activity
occurred in the period.

### Accumulating Snapshot Fact Tables

A row in an _accumulating snapshot fact table_ summarizes the measurement events at predictable
steps of a process with a defined start, standard intermediate steps, and defined end (e.g. order
fulfillment, claim processing). The table has a date foreign key per critical milestone, and a row —
created when the process begins, e.g. an order line — is revisited and updated as the pipeline
progresses; this ongoing update is unique to this fact table type. Besides milestone date keys,
these tables carry other dimensional foreign keys, optional degenerate dimensions, numeric lag
measurements at the grain, and milestone completion counters.

### Factless Fact Tables

Some events record only a set of dimensional entities coming together at a moment in time, with
no numeric result — a student attending a class, or a customer communication — yet a row with
foreign keys for calendar day, student, teacher, location, and class (say) is well-defined. _Factless
fact tables_ can also analyze what *didn't* happen: a factless coverage table listing every possible
event, and an activity table listing the events that did occur; subtracting activity from coverage
yields the events that never happened.

### Aggregate Fact Tables or Cubes

_Aggregate fact tables_ are numeric rollups of atomic fact data, built solely to speed up queries, and
should be available to the BI layer alongside the atomic tables so BI tools can smoothly pick the
right aggregate level at query time (_aggregate navigation_). This navigation must be _open_ so every
report writer, query tool, and BI application benefits equally — aggregates should behave like
database indexes, accelerating queries without being encountered directly by users. They carry
foreign keys to shrunken conformed dimensions and facts summed from the more atomic tables.
_Aggregate OLAP cubes_ are built the same way but are meant for direct user access.

### Consolidated Fact Tables

It is often convenient to combine facts from multiple processes at the same grain into a single
_consolidated fact table_ — e.g. sales actuals with sales forecasts — to make actual-vs-forecast
analysis simple and fast versus drilling across separate fact tables. This adds ETL burden but eases
the analytic burden for cross-process metrics that are frequently analyzed together.

## Basic Dimension Table Techniques

### Dimension Table Structure

Every dimension table has a single primary key, embedded as a foreign key wherever that
dimension row's context correctly describes a fact row. Dimension tables are usually wide, flat,
denormalized, with many low-cardinality text attributes — the most powerful ones carry verbose
descriptions — and are the primary target of query constraints and grouping; report labels are
typically dimension attribute values.

### Dimension Surrogate Keys

A dimension's primary key cannot be the operational system's natural key, because change
tracking over time produces multiple rows per natural key, and natural keys from different source
systems may be incompatible or poorly administered. Instead, claim control with anonymous
integer _dimension surrogate keys_, assigned in sequence from 1 whenever a new key is needed.
The date dimension is exempt: its more meaningful, predictable, and stable primary key is fine as
is.

### Natural, Durable, and Supernatural Keys

_Natural keys_ from operational source systems are subject to business rules outside the DW/BI
system's control (e.g. an employee number reused after a resignation and rehire). A _durable key_ —
persistent and unchanging in such cases — solves this; it is sometimes called a _durable
supernatural key_. The best durable keys are simple sequential integers, independent of the
originating business process. Multiple surrogate keys may attach to an entity as its profile
changes, but the durable key never does.

### Drilling Down

_Drilling down_ — the most fundamental analysis pattern — simply adds a row header, i.e. a
dimension attribute, to the GROUP BY of an existing query. The attribute can come from any
dimension attached to the fact table; no predetermined hierarchy or drill-down path is required.

### Degenerate Dimensions

A dimension can be defined with no content beyond its primary key — an invoice number, say, once
line-item facts inherit every descriptive foreign key from the invoice. This _degenerate dimension_ sits
in the fact table with the explicit acknowledgment that no dimension table backs it. Degenerate
dimensions are most common on transaction and accumulating snapshot fact tables.

### Denormalized Flattened Dimensions

Resist the normalization instincts of operational database design: denormalize many-to-one fixed
depth hierarchies into separate attributes on a flattened dimension row, in service of dimensional
modeling's twin goals of simplicity and speed.

### Multiple Hierarchies in Dimensions

Many dimensions hold more than one natural hierarchy — a calendar date's day-week-fiscal-period
hierarchy alongside its day-month-year one, or a location dimension's several geographic
hierarchies. Separate hierarchies coexist gracefully in the same dimension table.

### Flags and Indicators as Textual Dimension Attributes

Supplement cryptic abbreviations, true/false flags, and operational indicators in dimension tables
with full text words that carry meaning on their own. Break down operational codes with embedded
meaning into separate descriptive attributes, one per part of the code.

### Null Attributes in Dimensions

Null-valued dimension attributes arise from incomplete population, or from attributes that don't
apply to every row. Substitute a descriptive string such as "Unknown" or "Not Applicable" instead,
since databases handle null grouping and constraining inconsistently.

### Calendar Date Dimensions

_Calendar date dimensions_ attach to virtually every fact table, letting users navigate by familiar
dates, months, fiscal periods, and special days — you'd never compute Easter in SQL, but you
would look it up here. It typically carries many attributes (week number, month name, fiscal period,
national holiday indicator). Its primary key can be a meaningful integer (YYYYMMDD) rather than a
sequential surrogate, to aid partitioning, but still needs a special row for unknown/to-be-determined
dates. Filter and group on the dimension's attributes, not the smart key; when finer precision is
needed, add a standalone date/time stamp to the fact table (not a dimension foreign key). Add a
separate time-of-day dimension foreign key only if users constrain or group on time-of-day
attributes like day part or shift number.

### Role-Playing Dimensions

A single physical dimension can be referenced multiple times in one fact table, each reference a
logically distinct role — several dates in a fact table, each a foreign key to the date dimension.
Each foreign key needs its own independent view of the dimension, with uniquely named attribute
columns; these views are called roles.

### Junk Dimensions

Transactional processes often produce several miscellaneous, low-cardinality flags and indicators.
Rather than a separate dimension per flag, combine them into a single _junk dimension_ (often
called a _transaction profile dimension_), holding only the combinations of values that actually occur
in the source data, not their full Cartesian product.

### Snowflaked Dimensions

Normalizing a dimension's hierarchical relationships produces low-cardinality secondary tables
linked by attribute keys; repeating this across every hierarchy yields a multilevel _snowflake_.
Accurate as it is, avoid snowflaking — it is hard for business users to navigate and can hurt query
performance. A flattened, denormalized dimension table holds the same information more usably.

### Outrigger Dimensions

A dimension can reference another dimension table — a bank account dimension referencing the
date the account was opened, say. These _outrigger dimensions_ are permissible but should be used
sparingly; in most cases, demote the correlation to the fact table instead, with both dimensions as
separate foreign keys.

## Integration via Conformed Dimensions

### Conformed Dimensions

Dimension tables _conform_ when their attributes share column names and domain contents across
tables, letting separate fact tables be combined in a single report on that shared attribute — the
essence of DW/BI integration. When a conformed attribute drives the GROUP BY, results from
separate fact tables align on the same rows in a drill-across report. _Conformed dimensions_, defined
once with business data governance, are then reused across fact tables, delivering consistency and
avoiding repeated redevelopment.

### Shrunken Rollup Dimensions

_Shrunken dimensions_ are conformed dimensions holding a subset of a base dimension's rows and/or
columns. _Shrunken rollup_ dimensions are required for aggregate fact tables, and for business
processes captured at a higher grain than a related process (e.g. a forecast by month and brand,
versus sales at date and product). Another case: two dimensions at the same level of detail, one a
subset of the other's rows.

### Drilling Across

_Drilling across_ issues separate queries against two or more fact tables, each with row headers of
identical conformed attributes, then aligns the answer sets by sort-merging on the common
attribute — variously called stitch or multipass query by BI vendors.

### Value Chain

A _value chain_ is an organization's natural sequence of primary business processes — purchasing
to warehousing to retail sales for a retailer, or budgeting to commitments to payments for a general
ledger. Each step's operational source systems typically produce transactions or snapshots with
unique metrics, time intervals, and granularity, so each process usually spawns at least one atomic
fact table.

### Enterprise Data Warehouse Bus Architecture

The _bus architecture_ builds the DW/BI system incrementally by decomposing planning into
manageable pieces focused on business processes, while integrating via standardized conformed
dimensions reused across them. It is technology- and platform-independent — relational and OLAP
structures both participate — and it decomposes the program to encourage manageable agile
implementations matching the rows of the bus matrix.

### Enterprise Data Warehouse Bus Matrix

The _bus matrix_ is the essential design and communication tool for the bus architecture: rows are
business processes, columns are dimensions, and shaded cells mark whether a dimension applies
to a process. The design team scans each row to validate a candidate dimension and each column
to find dimensions needing conformance across processes; the matrix also helps prioritize DW/BI
projects, implemented one row at a time. A _detailed implementation bus matrix_ expands each row to
specific fact tables or cubes, with the precise grain and fact list documented at that level.

### Opportunity/Stakeholder Matrix

Once the bus matrix rows are set, draft a second matrix replacing the dimension columns with
business functions (marketing, sales, finance), shading cells to show which functions care about
which process rows. This _opportunity/stakeholder matrix_ helps identify who should join the
collaborative design sessions for each process.

## Slowly Changing Dimension Techniques

### Type 0: Retain Original

_Type 0_: the attribute value never changes, so facts always group by the original value.
Appropriate for anything labeled "original" (e.g. a customer's original credit score, a durable
identifier) and for most date dimension attributes.

### Type 1: Overwrite

_Type 1_: the old value is overwritten with the new one, so the attribute always reflects the latest
assignment and destroys history. Easy to implement and adds no rows, but any aggregate fact
table or OLAP cube built on it must be recomputed.

### Type 2: Add New Row

_Type 2_: a change adds a new dimension row with the updated attributes, so the primary key must
generalize beyond the natural/durable key to allow multiple rows per member. A new surrogate key
is assigned and used in fact tables from the update onward, until the next change. Add at least
three columns: row effective date/timestamp, row expiration date/timestamp, and a current-row
indicator.

### Type 3: Add New Attribute

_Type 3_: a new attribute preserves the old value while the main attribute is overwritten as in type 1
(sometimes called an "alternate reality"), letting users group or filter by either the current or the
prior value. Used relatively infrequently.

### Type 4: Add Mini-Dimension

_Type 4_: a group of rapidly changing attributes ("a rapidly changing monster dimension") is split off
into a _mini-dimension_ with its own primary key, carried in fact tables alongside the base
dimension's key. Good candidates are frequently used attributes on multimillion-row dimensions,
even if they don't change often.

### Type 5: Add Mini-Dimension and Type 1 Outrigger

_Type 5_ preserves historical attribute values while also reporting historical facts by current attribute
values: it builds on type 4 by embedding a current type 1 reference to the mini-dimension in the
base dimension, so currently assigned mini-dimension attributes are visible alongside the base
dimension's without a fact-table join. The base dimension and mini-dimension outrigger present
logically as one table; ETL must overwrite the type 1 reference whenever the current assignment
changes.

### Type 6: Add Type 1 Attributes to Type 2 Dimension

_Type 6_ also delivers both historical and current values, by building on type 2 with current type 1
copies of the same attributes embedded in the row, so fact rows can be filtered or grouped by the
value at the time of measurement or by today's value. The type 1 attribute is overwritten across
every row of a durable key whenever it's updated.

### Type 7: Dual Type 1 and Type 2 Dimensions

_Type 7_, the final hybrid, supports both as-was and as-is reporting from a single dimension table
modeled both ways, with both the durable key and the surrogate key placed in the fact table. The
type 1 perspective constrains the current flag and joins via the durable key; the type 2 perspective
joins via the surrogate key without constraining the flag. Each perspective is deployed to BI
applications as a separate view.

## Dealing with Dimension Hierarchies

### Fixed Depth Positional Hierarchies

A _fixed depth hierarchy_ is a series of many-to-one relationships (product → brand → category →
department) with agreed-upon level names; model it as separate positional attributes in the
dimension table. This is the easiest hierarchy to understand, navigate, and query at predictable,
fast performance — use it whenever the criteria hold. When depth varies or level names aren't
agreed, use a ragged hierarchy technique instead.

### Slightly Ragged/Variable Depth Hierarchies

_Slightly ragged_ hierarchies vary in depth but only a little — geographic hierarchies often range
three to six levels. Rather than the machinery for unpredictable variable hierarchies, force-fit them
into a fixed depth positional design sized to the maximum depth, populating attributes per business
rules.

### Ragged/Variable Depth Hierarchies

_Ragged hierarchies_ of indeterminate depth are hard to model and query relationally; SQL
extensions and OLAP languages offer limited recursive parent/child support (no substituting
alternative hierarchies at query time, no shared ownership structures, no time-varying hierarchies).
A specially constructed _bridge table_, with one row per possible path, overcomes these limits and
supports every form of traversal in standard SQL. Alternatively, a _pathstring attribute_ on the
dimension — a specially encoded string describing the full path from the hierarchy's root to that
row — handles most standard analysis requests in plain SQL, though it can't substitute alternative
or shared-ownership hierarchies and is vulnerable to relabeling if the hierarchy's structure changes.

## Advanced Fact Table Techniques

### Fact Table Surrogate Keys

Beyond dimension primary keys, a single-column _fact table surrogate key_ — not tied to any
dimension, assigned sequentially during ETL — is optional but useful: as the fact table's primary
key, as an immediate row identifier for ETL without navigating every dimension, to let an
interrupted load back out or resume, and to decompose fact table updates into safer insert-plus-
delete pairs.

### Centipede Fact Tables

Avoid _centipede fact tables_, which arise from separately normalizing each level of a many-to-one
hierarchy (date, month, quarter, year dimensions) and including all their foreign keys in one fact
table, or from embedding many low-cardinality dimension foreign keys instead of a junk dimension.
Collapse hierarchically related dimensions back to their unique lowest grain.

### Numeric Values as Attributes or Facts

A numeric value like a product's standard list price may fit either fact or dimension attribute. If it's
used mainly for calculation, put it in the fact table; if a stable value is used mainly for filtering and
grouping, make it a dimension attribute (optionally supplemented with value bands like $0–50). It
can sometimes usefully be both — e.g. a quantitative on-time-delivery metric and its qualitative
textual descriptor.

### Lag/Duration Facts

Accumulating snapshot fact tables capture multiple milestones, and users often want the lags or
durations between them — sometimes plain date differences, sometimes governed by more
complex business rules. Rather than making every query recompute each of the potentially many
possible lags, store one time lag per step measured against the process's start point; any lag
between two steps is then a simple subtraction of two stored values.

### Header/Line Fact Tables

Operational systems often pair a transaction header row with multiple lines. In these _header/line_
(_parent/child_) schemas, put all header-level dimension foreign keys and degenerate dimensions on
the line-level fact table.

### Allocated Facts

Header/line data often mixes granularities — a header freight charge, say. Allocate header facts
down to the line level per business rules so the allocated facts can be sliced and rolled up by every
dimension; a header-level fact table is usually unnecessary unless it aids query performance.

### Profit and Loss Fact Tables Using Allocations

Fact tables implementing the full profit equation — revenue minus costs equals profit — at the
atomic revenue-transaction grain, with many cost components, are among a DW/BI system's most
powerful deliverables, enabling rollups by customer, product, promotion, and channel profitability.
They are hard to build because cost components must be allocated from their original sources to
the fact grain — often a major, politically charged ETL subsystem needing high-level executive
support — so they are typically not tackled early in a program.

### Multiple Currency Facts

A fact table recording multi-currency financial transactions should carry a pair of columns per
financial fact: one in the transaction's true currency, one converted to a single standard currency
per an approved ETL business rule. The table also needs a currency dimension identifying the true
transaction currency.

### Multiple Units of Measure Facts

Some processes must report facts simultaneously in several units — pallets, ship cases, retail
cases, individual scan units in a supply chain. Store facts once at an agreed standard unit, plus
conversion factors between it and every other unit, and deploy views per user constituency using
the right factor. Keep the conversion factors in the underlying fact row so view calculations stay
simple and correct without adding query complexity.

### Year-to-Date Facts

Business users often ask for year-to-date values, but a single request easily grows into "YTD at
fiscal period close" or "fiscal period to date." Compute YTD metrics in the BI application or OLAP
cube instead of storing them as facts — more reliable and extensible than baking a fixed YTD into
the fact table.

### Multipass SQL to Avoid Fact-to-Fact Table Joins

A BI application must never join two fact tables directly across their foreign keys — the join's
cardinality can't be controlled in a relational database and will return incorrect results (e.g. joining
shipments and returns fact tables directly on customer and product). Instead, drill across: query
each fact table separately and sort-merge the results on the common row-header attributes.

### Timespan Tracking in Fact Tables

In isolated cases it helps to add a row effective date, row expiration date, and current-row indicator
to a fact table — the type 2 SCD pattern applied to facts — capturing the timespan a fact row was
effective. Unusual, but useful for scenarios like slowly changing inventory balances, where a plain
periodic snapshot would load identical rows every time.

### Late Arriving Facts

A fact row is _late arriving_ if the current dimensional context doesn't match it because the row itself
was delayed. Search the relevant dimensions for the keys that were in effect when the
measurement event actually occurred, rather than using today's dimension state.

## Advanced Dimension Table Techniques

### Dimension-to-Dimension Table Joins

Dimensions can reference other dimensions. Modeling this as an outrigger can cause explosive
growth in the base dimension, since a type 2 change in the outrigger forces type 2 processing in
the base too. Demoting the correlation — putting the outrigger's foreign key in the fact table instead
of the base dimension — often avoids this; the correlation is then discoverable only by traversing
the fact table, which is acceptable especially for a periodic snapshot where every dimension key is
guaranteed present each period.

### Multivalued Dimensions and Bridge Tables

A dimension is legitimately _multivalued_ when a fact row can have more than one value for it — a
patient with multiple simultaneous diagnoses, say. Attach it through a group dimension key to a
bridge table with one row per simultaneous value. A _multivalued bridge table_ may itself need to be
based on a type 2 SCD — e.g. the bridge implementing the many-to-many relationship between
bank accounts and customers usually needs type 2 account and customer dimensions, with
effective/expiration timestamps on the bridge and the querying application constrained to a specific
moment for a consistent snapshot.

### Behavior Tag Time Series

Nearly all warehouse text lives in dimension attributes. Periodic data-mining cluster analyses often
produce textual _behavior tags_; store the resulting time series of tags as positional attributes in the
customer dimension, plus an optional full-sequence text string. This positional design suits complex
simultaneous queries on the tags better than numeric computation would.

### Behavior Study Groups

Complex customer behavior sometimes requires lengthy iterative analysis that's impractical to
embed in every BI application wanting to filter on it. Capture the analysis's result as a simple table
of customers' durable keys — a _study group_ — usable as a filter on any schema with a customer
dimension by constraining to the group's keys at query time. Multiple study groups can combine via
intersection, union, and set difference.

### Aggregated Facts as Dimension Attributes

Users often want to constrain a customer dimension on aggregated performance — total spend last
year, or over the customer's lifetime. Place selected _aggregated facts_ in the dimension as
constraint targets and report row labels, often banded into ranges. This adds ETL burden but eases
the BI layer's analytic burden.

### Dynamic Value Banding

A _dynamic value banding report_ presents row headers as a progressive series of ranges over a
numeric fact — "Balance from $0 to $10," "Balance from $10.01 to $25," and so on — defined at
query time rather than during ETL. Implement the bands either as a small value-banding dimension
joined via greater-than/less-than to the fact table, or as an SQL CASE statement; the dimension join
is likely higher-performing, especially on a columnar database, since a CASE statement forces an
almost unconstrained scan of the fact table.

### Text Comments

Store freeform comments outside the fact table, in a separate comments dimension (or as
attributes of a dimension with one row per transaction, if the comments' cardinality matches
transaction count), with a corresponding fact-table foreign key — not as textual metrics inside the
fact table itself.

### Multiple Time Zones

To capture both universal standard time and local time in multi-time-zone applications, place dual
foreign keys in the affected fact tables, joining to two role-playing date (and potentially time-of-day)
dimensions.

### Measure Type Dimensions

When a fact table has a long, sparsely populated list of facts, it's tempting to collapse it to a single
generic fact identified by a _measure type dimension_. Generally avoid this: while it removes empty
columns, it multiplies the fact table's row count by the average number of occupied columns per
row and makes intra-column computation much harder. It's acceptable only when the number of
potential facts is extreme (hundreds), with few applicable to any given row.

### Step Dimensions

Sequential processes like web page events normally get a separate transaction fact row per step. A
_step dimension_ marks where a step sits in the overall process — its step number and how many
more steps remained to complete the session.

### Hot Swappable Dimensions

_Hot swappable dimensions_ pair the same fact table with different copies of an otherwise identical
dimension — for example, a stock-ticker-quote fact table exposed to multiple investors, each with
unique, proprietary attributes on the same stocks.

### Abstract Generic Dimensions

Avoid abstract generic dimensions — a single generic location dimension instead of separate
geographic attributes on store, warehouse, and customer dimensions, or one person dimension for
employees, customers, and vendor contacts because they're all human beings. Attribute sets
usually differ by type; common attributes (a geographic state) should still be uniquely labeled per
role (a store's state vs. a customer's). Merging every location, person, or product variety into one
dimension only produces a larger table. Such abstraction may suit the operational source or ETL
process, but it hurts query performance and legibility in the dimensional model.

### Audit Dimensions

When ETL creates a fact row, an _audit dimension_ can capture the processing metadata known at
that time — basic data-quality indicators (perhaps derived from an error event schema), the ETL
code version, and process execution timestamps. These attributes help compliance and auditing by
letting BI tools drill down to which software version produced which rows.

### Late Arriving Dimensions

Sometimes facts arrive minutes, hours, days, or weeks before their dimensional context — a
real-time inventory-depletion row may show a customer's natural key before that customer's
identity can be resolved. Post the row anyway with a special dimension row holding the unresolved
natural key as an attribute and generic "unknown" values elsewhere; when the real context arrives,
update the placeholder row with type 1 overwrites. Late arriving dimension data also occurs on
retroactive changes to type 2 attributes, which instead insert a new dimension row and require
restating the associated fact rows.

## Special Purpose Schemas

### Supertype and Subtype Schemas for Heterogeneous Products

Businesses with many disparate product lines — a retail bank's checking accounts, mortgages,
and business loans, all examples of "account" — can't build one consolidated fact table with the
union of every possible fact and dimension attribute; there can be hundreds of incompatible facts
and attributes. Instead build a single _supertype fact table_ with the intersection of facts across all
product types (plus a supertype dimension of common attributes), and separate _subtype_ fact
(and dimension) tables per product type. These are also called _core_ and _custom_ fact tables.

### Real-Time Fact Tables

_Real-time fact tables_ need updates more often than a traditional nightly batch, via techniques
depending on the DBMS or OLAP cube's capabilities — e.g. a "hot partition" pinned in memory
without aggregations or indexes, or deferred updating that lets running queries finish before
applying updates.

### Error Event Schemas

Managing data quality requires screens that test data as it flows from source systems to the BI
platform; a screen that detects an error records the event in a dimensional schema available only
in the ETL back room. This schema has an error event fact table at the grain of the individual error
event, and an associated error event detail fact table at the grain of each column in each table
involved in that event.
