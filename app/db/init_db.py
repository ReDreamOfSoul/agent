# -*- coding: utf-8 -*-
"""数据库初始化：创建表 + 种子数据（默认课程、教师、管理员、测试学生）

用法：
    python -m app.db.init_db          # 初始化（已存在则跳过种子数据）
    python -m app.db.init_db --reset  # 删库重建（危险！）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.database import Base, engine, SessionLocal  # noqa: E402
from app.db.models import (  # noqa: E402
    User, Course, Enrollment, Assignment, Announcement,
)
from app.core.auth import hash_password  # noqa: E402


def init_db(reset: bool = False) -> None:
    if reset:
        print("⚠️  删库重建...")
        Base.metadata.drop_all(bind=engine)

    print("创建数据表...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 检查是否已有种子数据
        if db.query(User).count() > 0:
            print("数据库已有用户数据，跳过种子数据插入。")
            return

        print("插入种子数据...")

        # 1. 默认管理员
        admin = User(
            username="admin",
            password_hash=hash_password("admin123"),
            role="admin",
            real_name="系统管理员",
            email="admin@example.com",
        )
        db.add(admin)
        db.flush()

        # 2. 默认教师：彭鹏
        teacher = User(
            username="pengpeng",
            password_hash=hash_password("pengpeng123"),
            role="teacher",
            real_name="彭鹏",
            email="pengpeng@cdut.edu.cn",
            teacher_id="T2025001",
        )
        db.add(teacher)
        db.flush()

        # 3. 学生账号（多个）
        students_data = [
            ("student01", "123456", "张三", "2025AI001", "人工智能2025级1班"),
            ("student02", "123456", "李四", "2025AI002", "人工智能2025级1班"),
            ("student03", "123456", "王五", "2025AI003", "人工智能2025级1班"),
            ("student04", "123456", "赵六", "2025AI004", "人工智能2025级2班"),
            ("student05", "123456", "钱七", "2025AI005", "人工智能2025级2班"),
            ("student", "student123", "测试学生", "2025AI068", "人工智能2025级1班"),
        ]
        student_ids = []
        for username, password, real_name, sid, cls in students_data:
            stu = User(
                username=username,
                password_hash=hash_password(password),
                role="student",
                real_name=real_name,
                email=f"{username}@example.com",
                student_id=sid,
                class_name=cls,
            )
            db.add(stu)
            db.flush()
            student_ids.append(stu.id)
        student = db.query(User).filter(User.username == "student").first()

        # 4. 默认课程：机器人学导论
        course = Course(
            course_code="L7043",
            course_name="机器人学导论",
            course_name_en="Introduction to Robotics",
            teacher_id=teacher.id,
            semester="2025-2026-1",
            credit=2.0,
            hours=32,
            description="本课程系统介绍机器人学的基本概念、运动学、动力学、轨迹规划、控制系统及编程方法。",
            textbook="《机器人技术基础》刘极峰、杨小兰，高等教育出版社，2019",
        )
        db.add(course)
        db.flush()

        # 5. 所有学生选课
        for sid in student_ids:
            enrollment = Enrollment(student_id=sid, course_id=course.id, status="active")
            db.add(enrollment)

        # 6. 示例公告
        announcement = Announcement(
            course_id=course.id,
            teacher_id=teacher.id,
            title="欢迎使用机器人学导论智能体",
            content="本智能体支持课程问答、作业批改、习题练习、视频课程学习等功能。如有问题请联系任课教师彭鹏。",
            priority="important",
            is_published=True,
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(announcement)

        # 7. 示例作业
        assignment = Assignment(
            course_id=course.id,
            teacher_id=teacher.id,
            title="第一章作业：机器人基本概念",
            description="简述机器人的定义、分类及典型应用场景。",
            content="1. 什么是机器人？机器人与普通自动化设备有何区别？\n2. 按应用领域分类，机器人可分为哪几类？各举一例。\n3. 简述工业机器人的主要组成部分。",
            total_score=100.0,
            status="published",
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(assignment)

        db.commit()
        print("✅ 数据库初始化完成！")
        print()
        print("默认账号：")
        print("  管理员：admin / admin123")
        print("  教师：  pengpeng / pengpeng123 （彭鹏）")
        print("  学生：  student / student123 （测试学生，学号 2025AI068）")
        print()
        print("默认课程：L7043 机器人学导论（彭鹏，2学分32学时）")

    except Exception as exc:
        db.rollback()
        print(f"❌ 初始化失败: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    init_db(reset=reset)
