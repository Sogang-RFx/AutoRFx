/** What a person is told when signing in or changing a password fails. Values are invented. */
import { describe, expect, it } from "vitest";
import { checkNewPassword, loginError, passwordError } from "@/lib/rfq/auth-messages";
import { ApiError } from "@/lib/rfq/provider";

const error = (status: number, code: string, message = "서버가 준 이유") => new ApiError("auth", status, code, message);

describe("login", () => {
  it("has one message for a wrong id and a wrong password", () => {
    expect(loginError(error(401, "invalid_credentials"))).toBe("아이디 또는 비밀번호가 맞지 않습니다.");
    expect(loginError(error(422, "invalid_request"))).toBe("아이디 또는 비밀번호가 맞지 않습니다.");
  });

  it("tells a lock, a refused address, a lost cookie and a dead backend apart", () => {
    const told = [
      error(429, "too_many_attempts"),
      error(403, "bad_origin"),
      error(401, "cookie_blocked"),
      error(0, "network"),
    ].map(loginError);
    expect(told[0]).toContain("시도가 너무 많아");
    expect(told[1]).toContain("이 주소에서는");
    expect(told[2]).toContain("localhost");
    expect(told[3]).toContain("연결하지 못했습니다");
    expect(new Set(told).size).toBe(4);
  });

  it("passes any other reason on", () => {
    expect(loginError(error(500, "internal"))).toBe("서버가 준 이유");
  });
});

describe("password change", () => {
  it("names a wrong current password and passes other reasons on", () => {
    expect(passwordError(error(401, "invalid_credentials"))).toBe("현재 비밀번호가 맞지 않습니다.");
    expect(passwordError(error(400, "weak_password"))).toBe("서버가 준 이유");
    expect(passwordError(error(403, "bad_origin"))).toContain("이 주소에서는");
  });

  it("checks the new password before it is sent", () => {
    expect(checkNewPassword("old-secret", "short", "short")).toContain("8자");
    expect(checkNewPassword("old-secret", "x".repeat(129), "x".repeat(129))).toContain("128자");
    expect(checkNewPassword("old-secret", "new-secret-1", "new-secret-2")).toContain("다릅니다");
    expect(checkNewPassword("old-secret", "old-secret", "old-secret")).toContain("지금과 다른");
    expect(checkNewPassword("old-secret", "new-secret-1", "new-secret-1")).toBeNull();
  });
});
