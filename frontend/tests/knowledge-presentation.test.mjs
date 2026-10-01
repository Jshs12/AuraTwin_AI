import test from "node:test";
import assert from "node:assert/strict";
import { knowledgeLoadFailure, knowledgeStatusTone, readableKnowledgeLabel } from "../src/utils/knowledgePresentation.ts";

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

test("knowledge load errors preserve distinct safe authorization and service states", () => {
  assert.match(knowledgeLoadFailure({ status: 401, endpoint: "/api/buildings/x/knowledge/documents" }).title, /Sign in again/);
  assert.match(knowledgeLoadFailure({ status: 403, endpoint: "/api/buildings/x/knowledge/documents" }).title, /cannot access/);
  assert.match(knowledgeLoadFailure({ status: 404 }).title, /building was not found/);
  assert.match(knowledgeLoadFailure({ status: 503 }).description, /configuration/);
  assert.match(knowledgeLoadFailure(new TypeError("Failed to fetch")).title, /connect/);
  assert.doesNotMatch(knowledgeLoadFailure({ status: 401, message: "token=secret" }).detail, /secret/);
});
