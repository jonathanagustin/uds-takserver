# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Apply the declared safety variant to exact published Lula 0.9.5 bytes.

No network or execution. The output must be beside its verified official input.
The workflow extracts the standalone crawl module and installs only its locked
external dependencies; callers may also patch an existing official installation.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import tarfile

INPUTS = {
    "bbc727ec333f3d61e9945407bdb62b3a0e3b48590766c5968adbc1dedf6d9612": "cli/commands/crawl.js",
    "6789580a1d7c5ab947733b658eec4f9d00e25b3ca410e7768e52682fd948a47f": "index.js",
}


def verify_archive(path):
    lock = json.loads(Path(__file__).with_name("source.lock.json").read_text())
    digest = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
    if digest != lock["tarballIntegrity"]:
        raise ValueError("Official Lula archive integrity mismatch")


def extract_crawl(archive_path, source_path):
    """Extract only the hashed crawl module and check its external versions."""
    verify_archive(archive_path)
    lock = json.loads(Path(__file__).with_name("source.lock.json").read_text())
    with tarfile.open(archive_path, "r:gz") as archive:
        def read(name):
            member = archive.getmember("package/" + name)
            if not member.isfile() or not 0 < member.size <= 1024 * 1024:
                raise ValueError("Expected a bounded regular official archive member")
            return archive.extractfile(member).read(1024 * 1024 + 1)
        raw = read("dist/cli/commands/crawl.js")
        if hashlib.sha256(raw).hexdigest() != lock["officialFiles"]["dist/cli/commands/crawl.js"]:
            raise ValueError("Unknown official crawl source digest")
        metadata = json.loads(read("package.json"))
        for name, version in lock["crawlRuntimeDependencies"].items():
            if metadata["dependencies"].get(name) != version:
                raise ValueError("Unexpected official crawl dependency version")
    with source_path.open("xb") as destination:
        destination.write(raw)


def patch_source(source):
    replacements = [
        ('    await octokit.issues.createComment({', '    return (await octokit.issues.createComment({'),
        ('      body\n    });\n    return;', '      body\n    })).data;'),
        ('        console.error(`Error checking deleted file ${file.filename}: ${err}`);',
         '        throw new Error("Incomplete Lula deleted-file analysis");'),
        ('      console.error(`Error processing ${file.filename}: ${err}`);',
         '      throw new Error("Incomplete Lula modified-file analysis");'),
        ('    const { owner, repo, pull_number } = getPRContext();',
         '    if (opts.postMode !== "comment") throw new Error("Safety variant supports comment mode only");\n    const { owner, repo, pull_number } = getPRContext();'),
        ('    const prBranch = pr.data.head.ref;', '''    const expected = JSON.parse(process.env.LULA_PREFLIGHT_JSON);
    const requireSnapshot = current => {
      if (expected.repository !== owner + "/" + repo || current.state !== "open" || current.draft !== false ||
          current.head?.repo?.full_name !== expected.repository || current.base?.ref !== "main" ||
          current.head.sha !== expected.headSHA || current.base.sha !== expected.baseSHA ||
          !Number.isInteger(current.changed_files) || current.changed_files < 0 || current.changed_files > 30 ||
          current.changed_files !== expected.changedFiles) throw new Error("PR changed after trusted preflight");
    };
    requireSnapshot(pr.data);
    const prBranch = pr.data.head.ref;'''),
        ('    const context = {',
         '    if (files.length !== expected.changedFiles) throw new Error("Incomplete Lula changed-file list");\n    const context = {'),
        ('      const finalBody = analysisResult.commentBody + closingBody;',
         '      requireSnapshot((await octokit.pulls.get({ owner, repo, pull_number })).data);\n      const finalBody = analysisResult.commentBody + closingBody;'),
        ('    await cleanupOldPosts(context, opts.postMode);\n', ''),
        ('      await postFinding({', '      const posted = await postFinding({'),
        ('        body: finalBody\n      });',
         '        body: finalBody\n      });\n      await deleteOldIssueComments({ ...context, posted });'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError("Official Lula source anchor mismatch")
        source = source.replace(old, new)
    start = source.index('async function deleteOldIssueComments({')
    end = source.index('async function deleteOldReviewComments({', start)
    source = source[:start] + '''async function deleteOldIssueComments({ octokit, owner, repo, pull_number, posted }) {
  // The successful create response identifies this token's actual author.
  const bot = posted?.user;
  if (bot?.type !== "Bot" || bot.login !== "github-actions[bot]" || bot.id !== 41898282 ||
      !Number.isSafeInteger(posted.id) || posted.id <= 0) {
    throw new Error("Replacement comment is not authored by the expected GitHub Actions bot");
  }
  // Finish enumeration before deletion; changing pages during traversal skips entries.
  const stale = [];
  for (let page = 1; ; page++) {
    const { data: comments } = await octokit.issues.listComments({
      owner, repo, issue_number: pull_number, per_page: 100, page
    });
    if (!comments.length) break;
    for (const c of comments) {
      if (c.id < posted.id && Number.isSafeInteger(c.id) && c.id > 0 &&
          c.user?.id === bot.id && c.user?.login === bot.login && c.user?.type === bot.type &&
          typeof c.body === "string" &&
          c.body.startsWith(LULA_SIGNATURE + "\\n## Lula Compliance Overview\\n")) stale.push(c.id);
    }
  }
  for (const comment_id of stale) await octokit.issues.deleteComment({ owner, repo, comment_id });
}
''' + source[end:]
    return source


def prepare(source_path, output_path):
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("Expected a regular official source file")
    if output_path.parent.resolve() != source_path.parent.resolve():
        raise ValueError("Output must be beside the official source")
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() not in INPUTS:
        raise ValueError("Unknown official Lula source digest")
    patched = patch_source(raw.decode()).encode()
    with output_path.open("xb") as destination:
        destination.write(patched)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--extract-crawl", action="store_true")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verify_archive(args.archive)
    if args.extract_crawl:
        extract_crawl(args.archive, args.source)
    prepare(args.source, args.output)
