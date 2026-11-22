const assert = require("node:assert/strict");
const { test } = require("node:test");
const { hasTitleOnlyReferences, reportEvidenceIds } = require(process.argv[2]);

test("recognizes title-only references", () => {
  const content = "Answer\n\n## 参考文献\n\n- \\[1\\] [Study](https://example.org/article)";
  assert.equal(hasTitleOnlyReferences(content), true);
  assert.equal(reportEvidenceIds(content).size, 0);
});

test("preserves identifier matching for old reports and evidence without references", () => {
  const content = "## 参考文献\n\n- \\[1\\] Study · 证据 a · 论文摘要";
  assert.equal(hasTitleOnlyReferences(content), false);
  assert.deepEqual([...reportEvidenceIds(content)], ["a"]);
  assert.equal(hasTitleOnlyReferences("Report without references"), false);
});
