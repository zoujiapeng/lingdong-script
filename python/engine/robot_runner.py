"""
Robot Framework 执行器 — 将 DSL 转为 .robot 并执行。

用法:
    from engine.robot_runner import run_robot
    result = run_robot("CLICK 100 200\\nWAIT 1\\nTYPE hello")
    print(result["success"], result["logs"])
"""

import os
import tempfile
import time
import xml.etree.ElementTree as ET

from engine.robot_converter import dsl_to_robot


def run_robot(dsl_text: str, task_name: str = "Lobster Automation",
              output_dir: str = None) -> dict:
    """将 DSL 文本转为 .robot 并执行，返回统一格式结果。

    Args:
        dsl_text: Lobster DSL source.
        task_name: Name for the Robot Framework task.
        output_dir: Output directory for robot logs/reports. Auto temp if None.

    Returns:
        dict{success, logs: list[{level, message, timestamp}],
             passed: int, failed: int, elapsed_ms: float,
             robot_output: str, robot_log: str, robot_report: str}
    """
    start = time.time()
    logs = []
    temp_root = None

    def _log(level: str, msg: str):
        logs.append({"level": level, "message": msg, "timestamp": time.time()})
        print(f"[robot_runner] {level}: {msg}")

    try:
        # 1. Convert DSL → .robot
        _log("info", "Converting DSL to Robot Framework format...")
        # Resolve library path to absolute (RF runs from temp dir)
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        lib_path = os.path.join(project_root, "python", "rpa_keywords", "desktop_actions.py").replace("\\", "/")
        robot_content = dsl_to_robot(dsl_text, task_name, library_path=lib_path)

        # 2. Write to temp .robot file
        if output_dir is None:
            temp_root = tempfile.TemporaryDirectory(prefix="lobster_robot_")
            output_dir = temp_root.name

        robot_file = os.path.join(output_dir, "lobster_task.robot")
        with open(robot_file, "w", encoding="utf-8") as f:
            f.write(robot_content)
        _log("info", f"Robot file written to {robot_file}")

        # 3. Execute via robot.run()
        _log("info", "Starting Robot Framework execution...")
        try:
            from robot import run as robot_run
        except ImportError:
            from robot.run import run as robot_run

        # robot.run() returns 0 on success, 1+ on failure
        rc = robot_run(
            robot_file,
            outputdir=output_dir,
        )
        elapsed = (time.time() - start) * 1000

        # 4. Parse results from output.xml
        output_xml = os.path.join(output_dir, "output.xml")
        log_html = os.path.join(output_dir, "log.html")
        report_html = os.path.join(output_dir, "report.html")

        passed = 0
        failed = 0
        test_logs = []

        if os.path.exists(output_xml):
            try:
                tree = ET.parse(output_xml)
                root = tree.getroot()
                for suite in root.iter("suite"):
                    for test in suite.iter("test"):
                        status_el = test.find("status")
                        test_name = test.get("name", "")
                        test_status = status_el.get("status", "FAIL") if status_el is not None else "FAIL"

                        if test_status == "PASS":
                            passed += 1
                        else:
                            failed += 1

                        # Extract keyword-level messages
                        for kw in test.iter("kw"):
                            kw_name = kw.get("name", "")
                            for msg in kw.iter("msg"):
                                text = (msg.text or "").strip()
                                level = msg.get("level", "INFO")
                                if text:
                                    test_logs.append({
                                        "keyword": kw_name,
                                        "level": level,
                                        "message": text,
                                    })

            except Exception as e:
                _log("warn", f"Failed to parse output.xml: {e}")

        success = rc == 0 and failed == 0
        _log("info", f"Robot execution complete: rc={rc}, passed={passed}, failed={failed}")

        result = {
            "success": success,
            "logs": logs,
            "test_logs": test_logs,
            "passed": passed,
            "failed": failed,
            "elapsed_ms": round(elapsed, 1),
            "robot_output": output_xml if os.path.exists(output_xml) else "",
            "robot_log": log_html if os.path.exists(log_html) else "",
            "robot_report": report_html if os.path.exists(report_html) else "",
            "return_code": rc,
        }
        return result

    except ImportError as e:
        _log("error", f"Robot Framework not installed: {e}")
        return {"success": False, "logs": logs, "error": str(e), "elapsed_ms": 0}

    except Exception as e:
        _log("error", f"Robot execution failed: {e}")
        return {"success": False, "logs": logs, "error": str(e),
                "elapsed_ms": round((time.time() - start) * 1000, 1)}

    finally:
        if temp_root is not None:
            try:
                temp_root.cleanup()
            except Exception:
                pass


if __name__ == "__main__":
    # CLI test
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            dsl = f.read()
    else:
        dsl = "CLICK 100 200\nWAIT 1\nTYPE Hello Robot\nSCREENSHOT"

    result = run_robot(dsl)
    print(f"\nResult: success={result['success']}, "
          f"passed={result['passed']}, failed={result['failed']}, "
          f"elapsed={result.get('elapsed_ms', 0)}ms")
