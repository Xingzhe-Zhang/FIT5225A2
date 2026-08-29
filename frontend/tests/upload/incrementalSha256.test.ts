import { expect, test } from "vitest";

import { IncrementalSha256 } from "../../src/upload/incrementalSha256";

test("matches standard SHA-256 vectors across arbitrary chunk boundaries", () => {
  expect(new IncrementalSha256().digestHex()).toBe(
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  );
  const digest = new IncrementalSha256()
    .update(new TextEncoder().encode("a"))
    .update(new TextEncoder().encode("b"))
    .update(new TextEncoder().encode("c"))
    .digestHex();
  expect(digest).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

test("hashes a payload spanning multiple blocks incrementally", () => {
  const hasher = new IncrementalSha256();
  const payload = new TextEncoder().encode("0123456789".repeat(1000));
  for (let offset = 0; offset < payload.length; offset += 37) {
    hasher.update(payload.subarray(offset, offset + 37));
  }
  expect(hasher.digestHex()).toBe("4c207598af7a20db0e3334dd044399a40e467cb81b37f7ba05a4f76dcbd8fd59");
});
