import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_auth_and_subjects_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register User
        user_email = "teststudent@example.com"
        register_payload = {
            "email": user_email,
            "full_name": "Test Student",
            "password": "securepassword123"
        }
        res = await ac.post("/api/v1/auth/register", json=register_payload)
        assert res.status_code == 201
        data = res.json()
        assert "access_token" in data
        token = data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Login User
        login_payload = {
            "email": user_email,
            "password": "securepassword123"
        }
        res = await ac.post("/api/v1/auth/login", json=login_payload)
        assert res.status_code == 200
        assert "access_token" in res.json()

        # 3. Invalid Login
        res = await ac.post("/api/v1/auth/login", json={"email": user_email, "password": "wrong"})
        assert res.status_code == 401

        # 4. Get Profile (/me)
        res = await ac.get("/api/v1/auth/me", headers=headers)
        assert res.status_code == 200
        assert res.json()["email"] == user_email

        # 5. Create Subject
        subject_payload = {
            "title": "Computer Science 101",
            "description": "Data Structures & Algorithms",
            "color": "#0284c7"
        }
        res = await ac.post("/api/v1/subjects", json=subject_payload, headers=headers)
        assert res.status_code == 201
        subj_data = res.json()
        assert subj_data["title"] == "Computer Science 101"
        subject_id = subj_data["id"]

        # 6. List Subjects
        res = await ac.get("/api/v1/subjects", headers=headers)
        assert res.status_code == 200
        subjects = res.json()
        assert len(subjects) >= 1
        assert any(s["id"] == subject_id for s in subjects)

        # 7. Delete Subject
        res = await ac.delete(f"/api/v1/subjects/{subject_id}", headers=headers)
        assert res.status_code == 204
