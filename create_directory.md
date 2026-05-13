To ensure uploaded photos and videos (for enrollment, attendance, and annotations) persist across Docker container restarts and removals, a directory named `static_data` must be created on your host machine. This directory is mounted into the `/app/static` path within the `web` service container, making all static uploads persistent.

**Action Required:**
Create a directory named `static_data` in the root of your project:
```bash
mkdir static_data
```
This step is crucial for data persistence.
