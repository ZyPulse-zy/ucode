#!/usr/bin/env python3
"""Independent, pinned ucode FFI cross-build and QEMU evidence job."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


PINS = {
    "baseline": "d2e8b8d2052778557ccca7e781d2ad423324961b",
    "master": "4a28662538275b67a0bcf11de25c365c1ec7a99d",
    "pr503": "079a4de01b38701de88f5b560f68bc4d2c466cfa",
}
TARGETS = {
    "powerpc_8548": {
        "target": "mpc85xx/p1010", "sdk": "mpc85xx-p1010",
        "sha256": "626ef386778f6c042eca098715cf923110461333a9b847a7073a9bb8fda43de2",
        "flags": ["-mcpu=8548", "-msoft-float"],
        "qemu": "qemu-ppc-static", "cpu": "e500v2", "machine": 20,
    },
    "powerpc_464fp": {
        "target": "apm821xx/nand", "sdk": "apm821xx-nand",
        "sha256": "7176bf8e8427f38948251a67ee9c5bfd7caa3dbe89b51b3d17ed417cbf8afb6d",
        "flags": ["-mcpu=464fp"],
        "qemu": "qemu-ppc-static", "cpu": "460ex", "machine": 20,
    },
    "riscv64_generic": {
        "target": "d1/generic", "sdk": "d1-generic",
        "sha256": "d5e6c487cc0e86a4807fa4a4cad4cdf34c6a3d52236c5814cbe1ee24981a0e84",
        "flags": ["-mabi=lp64d", "-march=rv64gc"],
        "qemu": "qemu-riscv64-static", "cpu": None, "machine": 243,
    },
}
DEPS = {
    "libffi-3.4.7.tar.gz": "138607dee268bdecf374adf9144c00e839e38541f75f24a1fcf18b78fda48b2d",
    "json-c-0.19-nodoc.tar.gz": "704927172443309a8efeb162060bb215548e1286e5568514007dd2cc35a0a164",
    "libmd-1.2.0.tar.xz": "ac15ffb8430502fbaccdec66c5a82ee0eab0b0f36220df56710feadfeb13d0a0",
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require(value, message):
    if not value:
        raise RuntimeError(message)


class Job:
    def __init__(self, args):
        self.a = args
        self.cfg = TARGETS[args.arch]
        self.work = args.work_dir.resolve()
        self.out = args.output_dir.resolve()
        self.cache = args.cache_dir.resolve()
        for p in (self.work, self.out, self.cache):
            p.mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy()
        self.summary = {"architecture": args.arch, "pins": PINS, "steps": []}

    def run(self, name, cmd, cwd=None, env=None, expected=0):
        cmd = [str(x) for x in cmd]
        result = subprocess.run(cmd, cwd=cwd or self.work, env=env or self.env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (self.out / (name + ".log")).write_text("COMMAND " + shlex.join(cmd) + "\n" + result.stdout)
        self.summary["steps"].append({"name": name, "returncode": result.returncode})
        self.save()
        if expected is not None:
            require(result.returncode == expected,
                    f"{name}: exit {result.returncode}, expected {expected}; see log")
        return result

    def save(self):
        (self.out / "summary.json").write_text(json.dumps(self.summary, indent=2) + "\n")

    def archive(self, url, digest):
        dest = self.cache / url.rsplit("/", 1)[-1]
        if not dest.is_file():
            temp = dest.with_suffix(dest.suffix + ".partial")
            self.run("download-" + dest.name,
                     ["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "30",
                      "--max-time", "900", "--output", temp, url])
            require(sha(temp) == digest, f"download hash mismatch: {url}")
            temp.rename(dest)
        require(sha(dest) == digest, f"cache hash mismatch: {dest}")
        self.summary.setdefault("archives", []).append({"url": url, "sha256": digest, "bytes": dest.stat().st_size})
        self.save()
        return dest

    def extract(self, archive, dest):
        require(not dest.exists(), f"build directory already exists: {dest}")
        dest.mkdir(parents=True)
        self.run("extract-" + dest.name, ["tar", "-xf", archive, "--strip-components=1", "-C", dest])
        return dest

    def sources(self):
        sources = {}
        for name, commit in PINS.items():
            if self.a.source_cache:
                sources[name] = self.a.source_cache.resolve() / ("source" if name == "master" else name)
            else:
                source = self.work / "sources" / name
                source.mkdir(parents=True)
                self.run("git-init-" + name, ["git", "init", source])
                self.run("git-fetch-" + name, ["git", "-C", source, "fetch", "--depth=1",
                         "https://github.com/ucode-lang/ucode.git", commit])
                self.run("git-checkout-" + name, ["git", "-C", source, "checkout", "--detach", "FETCH_HEAD"])
                sources[name] = source
            actual = self.run("source-pin-" + name, ["git", "-C", sources[name], "rev-parse", "HEAD"]).stdout.strip()
            require(actual == commit, f"wrong {name} source revision")
        candidate = self.a.candidate.resolve()
        actual = self.run("candidate-pin", ["git", "-C", candidate, "rev-parse", "HEAD"]).stdout.strip()
        require(actual == self.a.candidate_sha, "candidate SHA differs from explicit input")
        require(not self.run("candidate-clean", ["git", "-C", candidate, "status", "--porcelain", "--untracked-files=no"]).stdout,
                "candidate has uncommitted tracked changes")
        for name in ("CMakeLists.txt", "lib/ffi/ffi.c", "tests/custom/run_tests.uc"):
            require(sha(candidate / name) == sha(sources["pr503"] / name),
                    "test-only candidate changes runtime/runner: " + name)
        self.summary["candidate_sha"] = actual
        self.summary["candidate_test_sha256"] = sha(candidate / "tests/custom/17_lib_ffi/35_complex_numbers")
        self.save()
        sources["candidate"] = candidate
        return sources

    def build_deps(self, name, sources, cc, flags, target=None):
        root = self.work / name
        root.mkdir()
        prefix = root / "prefix"
        env = self.env.copy()
        env.update(CC=str(cc), CXX=str(cc).removesuffix("gcc") + "g++",
                   CFLAGS=" ".join(["-Os", "-fPIC", *flags]),
                   PKG_CONFIG_LIBDIR=str(prefix / "lib/pkgconfig"), PKG_CONFIG_PATH="", PKG_CONFIG_SYSROOT_DIR="")
        if target:
            env.update(STAGING_DIR=str(self.sdk / "staging_dir"),
                       AR=str(self.tc / "bin" / (target + "-ar")),
                       RANLIB=str(self.tc / "bin" / (target + "-ranlib")))
        for dep in ("libffi-3.4.7", "libmd-1.2.0"):
            build = root / dep
            build.mkdir()
            cmd = [sources[dep] / "configure", "--prefix=" + str(prefix), "--libdir=" + str(prefix / "lib")]
            if target:
                cmd.append("--host=" + target)
            if dep.startswith("libffi"):
                cmd += ["--disable-docs", "--disable-multi-os-directory", "--disable-raw-api", "--disable-structs"]
            self.run(name + "-" + dep + "-configure", cmd, cwd=build, env=env)
            self.run(name + "-" + dep + "-build", ["make", "-j" + str(self.a.jobs)], cwd=build, env=env)
            self.run(name + "-" + dep + "-install", ["make", "install"], cwd=build, env=env)
        js = root / "json-c"
        opts = ["cmake", "-S", sources["json-c-0.19"], "-B", js,
                "-DCMAKE_C_COMPILER=" + str(cc), "-DCMAKE_C_FLAGS=" + env["CFLAGS"],
                "-DCMAKE_INSTALL_PREFIX=" + str(prefix), "-DCMAKE_INSTALL_LIBDIR=lib",
                "-DBUILD_TESTING=OFF", "-DBUILD_SHARED_LIBS=ON", "-DBUILD_STATIC_LIBS=ON", "-DDISABLE_WERROR=ON"]
        if target:
            opts.append("-DCMAKE_SYSTEM_NAME=Linux")
        self.run(name + "-json-c-configure", opts, env=env)
        self.run(name + "-json-c-build", ["cmake", "--build", js, "-j" + str(self.a.jobs)], env=env)
        self.run(name + "-json-c-install", ["cmake", "--install", js], env=env)
        return prefix, env

    def ucode(self, name, source, prefix, env, cross):
        build = self.work / ("ucode-" + name)
        opts = ["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_PREFIX_PATH=" + str(prefix), "-DFFI_SUPPORT=ON", "-DDIGEST_SUPPORT=ON",
                "-DUBUS_SUPPORT=OFF", "-DUCI_SUPPORT=OFF", "-DRTNL_SUPPORT=OFF",
                "-DNL80211_SUPPORT=OFF", "-DULOOP_SUPPORT=OFF", "-DZLIB_SUPPORT=OFF"]
        # Upstream capability checks link with -lffi, including native builds.
        # Keep checks and modules on the same independently built dependency.
        for kind in ("EXE", "SHARED", "MODULE"):
            opts.append(f"-DCMAKE_{kind}_LINKER_FLAGS=-L{prefix / 'lib'}")
        if cross:
            opts += ["-DCMAKE_SYSTEM_NAME=Linux", "-DCMAKE_C_COMPILER=" + str(self.cc),
                     "-DCMAKE_C_FLAGS=" + " ".join(["-Os", "-fPIC", *self.cfg["flags"]]),
                     "-DCMAKE_FIND_ROOT_PATH=" + str(prefix) + ";" + str(self.tc),
                     "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
                     "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY"]
        self.run(name + "-configure", opts, env=env)
        result = self.run(name + "-build", ["cmake", "--build", build, "-j" + str(self.a.jobs)], env=env, expected=None)
        if name in ("baseline", "master"):
            require(result.returncode != 0 and "ffi_type_complex_float" in result.stdout
                    and "ffi_type_complex_double" in result.stdout, name + " did not reproduce the specific compiler failure")
        else:
            require(result.returncode == 0 and (build / "ucode").is_file() and (build / "ffi.so").is_file(),
                    name + " ucode/FFI build failed")
        shutil.copy2(build / "CMakeCache.txt", self.out / (name + "-CMakeCache.txt"))
        return build

    def wrappers(self, runtime, prefix):
        qemu = self.a.qemu or shutil.which(self.cfg["qemu"])
        require(qemu, "missing QEMU binary: " + self.cfg["qemu"])
        self.run("qemu-version", [qemu, "--version"])
        cmd = [qemu]
        if self.cfg["cpu"]:
            cmd += ["-cpu", self.cfg["cpu"]]
        cmd += ["-L", self.tc, "-E", "LD_LIBRARY_PATH=" + ":".join(map(str, (runtime, prefix / "lib", self.tc / "lib"))), runtime / "ucode"]
        wrappers = self.work / "wrappers"
        wrappers.mkdir()
        self.target = wrappers / "ucode-target"
        self.target.write_text("#!/bin/sh\nexec " + shlex.join(map(str, cmd)) + ' "$@"\n')
        self.target.chmod(0o755)
        self.trace = self.out / "fixture-compilers.jsonl"
        cc = wrappers / "cc"
        cc.write_text("#!/usr/bin/env python3\n" +
            "import hashlib,json,os,pathlib,subprocess,sys\n" +
            f"cmd={str(self.cc)!r};flags={self.cfg['flags']!r};trace={str(self.trace)!r}\n" +
            f"env=os.environ.copy();env['STAGING_DIR']={str(self.sdk / 'staging_dir')!r}\n" +
            "r=subprocess.run([cmd,*flags,*sys.argv[1:]],env=env)\n" +
            "row={'args':sys.argv[1:],'returncode':r.returncode,'sources':{x:hashlib.sha256(pathlib.Path(x).read_bytes()).hexdigest() for x in sys.argv[1:] if x.endswith('.c') and pathlib.Path(x).is_file()}}\n" +
            "if '-o' in sys.argv:\n p=pathlib.Path(sys.argv[sys.argv.index('-o')+1])\n if p.is_file():\n  b=p.read_bytes();row.update(sha256=hashlib.sha256(b).hexdigest(),magic=b[:4].hex(),elf_machine=int.from_bytes(b[18:20],'little' if b[5]==1 else 'big'))\n" +
            "with open(trace,'a') as f:f.write(json.dumps(row)+'\\n')\n" +
            "sys.exit(r.returncode)\n")
        cc.chmod(0o755)
        return wrappers

    def runtime_tests(self, sources, runtime, prefix, host, hostprefix, wrappers):
        env = self.env.copy()
        env.update(LD_LIBRARY_PATH=str(hostprefix / "lib"), UCODE_BIN=str(self.target),
                   UCODE_LIB=str(runtime), PATH=str(wrappers) + os.pathsep + env["PATH"])
        result = self.run("target-smoke", [self.target, "-L", str(runtime / "*.so"), "-e",
                          'import * as ffi from "ffi"; print(ffi.sizeof("int")); print(ffi.C.dlsym("ffi_type_complex_double") == null);'], env=env)
        require(result.stdout == "4true", "unexpected target FFI size/capability")
        fixture = self.work / "private-runtime.so"
        script_dir = Path(__file__).resolve().parent
        self.run("private-fixture-build", [wrappers / "cc", "-shared", "-fPIC", script_dir / "fixture.c", "-o", fixture], env=env)
        result = self.run("private-runtime", [self.target, "-L", str(runtime / "*.so"), script_dir / "checks.uc", fixture], env=env)
        passed = json.loads(result.stdout)
        require(len(passed) == 15 and len(set(passed)) == 15, "private runtime must pass all 15 assertions")
        self.summary["private_runtime_checks"] = passed
        for variant in ("pr503", "candidate"):
            before = len(self.trace.read_text().splitlines())
            # The runner enumerates tests relative to its own source path.
            # Its bytes are verified identical, but use the matching checkout.
            runner = sources[variant] / "tests/custom/run_tests.uc"
            cmd = [host / "ucode", "-L", str(host / "*.so"), "-S", runner,
                   sources[variant] / "tests/custom/17_lib_ffi/35_complex_numbers"]
            result = self.run(variant + "-test35", cmd, env=env, expected=None)
            new = [json.loads(x) for x in self.trace.read_text().splitlines()[before:]]
            if variant == "pr503":
                require(result.returncode == 3 and "FAILED (3/3)" in result.stdout,
                        "original complex cases must fail, not skip or pass")
                require(len(new) == 3 and all(x.get("returncode") == 0 and x.get("magic") == "7f454c46"
                        and x.get("elf_machine") == self.cfg["machine"] for x in new),
                        "original tests must compile three genuine target ELF fixtures")
                require("don't know how to handle argument type" in result.stdout
                        and "don't know how to handle return type" in result.stdout,
                        "original tests failed for an unrelated reason")
            else:
                require(result.returncode == 0 and "OK (3/3 skipped)" in result.stdout and not new,
                        "candidate must feature-skip exactly three cases before compiling fixtures")
        self.summary["original_complex_cases_failed"] = 3
        self.summary["candidate_complex_cases_feature_skipped"] = 3
        self.summary["complex_abi_success_claimed"] = False
        binaries = self.out / "binaries"
        binaries.mkdir(exist_ok=True)
        for path in (runtime / "ucode", runtime / "ffi.so", runtime / "libucode.so", fixture):
            dest = binaries / path.name
            shutil.copy2(path, dest)
            header = dest.read_bytes()[:20]
            machine = int.from_bytes(header[18:20], "little" if header[5] == 1 else "big")
            require(header[:4] == b"\x7fELF" and machine == self.cfg["machine"], "wrong archived target ELF")
            self.summary.setdefault("binaries", []).append({"path": str(path), "artifact": str(dest.relative_to(self.out)),
                "sha256": sha(dest), "bytes": dest.stat().st_size, "elf_machine": machine})
        for name in ("fixture.c", "checks.uc"):
            shutil.copy2(script_dir / name, self.out / name)
        for name in ("ffi.h", "ffitarget.h"):
            shutil.copy2(prefix / "include" / name, self.out / name)
        self.save()

    def execute(self):
        sources = self.sources()
        dependency_sources = {}
        for name, digest in DEPS.items():
            archive = self.archive("https://sources.cdn.openwrt.org/" + name, digest)
            directory = "json-c-0.19" if name.startswith("json-c") else name.split(".tar.")[0]
            dependency_sources[directory] = self.extract(archive, self.work / "dependencies" / directory)
        if self.a.sdk_directory:
            self.sdk = self.a.sdk_directory.resolve()
            self.summary["sdk_reused_read_only"] = str(self.sdk)
        else:
            cfg = self.cfg
            name = f"openwrt-sdk-{cfg['sdk']}_gcc-14.4.0_musl.Linux-x86_64.tar.zst"
            url = f"https://downloads.openwrt.org/snapshots/targets/{cfg['target']}/{name}"
            self.sdk = self.extract(self.archive(url, cfg["sha256"]), self.work / "sdk")
        candidates = list((self.sdk / "staging_dir").glob("toolchain-*"))
        require(len(candidates) == 1, "expected exactly one SDK target toolchain")
        self.tc = candidates[0]
        compilers = list((self.tc / "bin").glob("*-linux-musl-gcc"))
        require(len(compilers) == 1, "expected one exact Linux-musl compiler")
        self.cc = compilers[0]
        version = self.run("compiler-version", [self.cc, "--version"]).stdout
        require("14.4.0" in version, "unexpected compiler version")
        target = self.cc.name.removesuffix("-gcc")
        prefix, env = self.build_deps("target-deps", dependency_sources, self.cc, self.cfg["flags"], target)
        macros = self.run("ffi-target-macros", [self.cc, *self.cfg["flags"], "-I" + str(prefix / "include"),
                          "-x", "c", "-dM", "-E", "-include", "ffi.h", "/dev/null"], env=env).stdout
        require("FFI_TARGET_HAS_COMPLEX_TYPE" not in macros, "target unexpectedly supports complex ABI")
        for name in PINS:
            runtime = self.ucode(name, sources[name], prefix, env, cross=True)
        hostprefix, hostenv = self.build_deps("host-deps", dependency_sources, "gcc", [])
        host = self.ucode("host", sources["pr503"], hostprefix, hostenv, cross=False)
        wrappers = self.wrappers(runtime, prefix)
        self.runtime_tests(sources, runtime, prefix, host, hostprefix, wrappers)
        self.summary["result"] = "passed"
        self.summary["scope"] = "Pinned interpreter/FFI builds, QEMU runtime and test35 only; not a whole SDK package or full-suite pass"
        self.save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arch", choices=TARGETS, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--work-dir", type=Path, default=Path("cross-build"))
    p.add_argument("--output-dir", type=Path, default=Path("cross-evidence"))
    p.add_argument("--cache-dir", type=Path, default=Path("download-cache"))
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--qemu", help="Override qemu-user-static executable")
    p.add_argument("--sdk-directory", type=Path, help="Read-only reuse of a previously SHA-verified extracted SDK")
    p.add_argument("--source-cache", type=Path, help="Read-only pinned baseline/source/pr503 Git worktrees")
    a = p.parse_args()
    require(a.jobs > 0, "jobs must be positive")
    require(len(a.candidate_sha) == 40 and all(c in "0123456789abcdef" for c in a.candidate_sha), "candidate requires a full commit SHA")
    job = Job(a)
    try:
        job.execute()
    except Exception as error:
        job.summary["result"] = "failed"
        job.summary["error"] = str(error)
        job.save()
        raise


if __name__ == "__main__":
    main()
