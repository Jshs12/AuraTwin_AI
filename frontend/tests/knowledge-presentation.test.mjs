import test from "node:test";
import assert from "node:assert/strict";
import { knowledgeStatusTone, readableKnowledgeLabel } from "../src/utils/knowledgePresentation.ts";

test("knowledge labels use operator-readable words", () => {
  assert.equal(readableKnowledgeLabel("MAINTENANCE_PROCEDURE"), "Maintenance Procedure");
  assert.equal(readableKnowledgeLabel("LOCAL_EXTRACTOR"), "Local Extractor");
});

test("knowledge lifecycle tones distinguish ready, waiting, failed and archived", () => {
  assert.equal(knowledgeStatusTone("READY"), "success");
  assert.equal(knowledgeStatusTone("REGISTERED"), "warning");
  assert.equal(knowledgeStatusTone("FAILED"), "danger");
  assert.equal(knowledgeStatusTone("ARCHIVED"), "neutral");
});
