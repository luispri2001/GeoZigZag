from public_map_generator.jobs import job_progress


def test_job_progress_ignores_unavailable_components():
    payload = {
        "components": {
            "elevation": {"state": "available"},
            "osm": {"state": "error"},
            "terrain": {"state": "processing"},
            "soil_moisture": {"state": "unavailable"},
        }
    }

    percent, completed, errors = job_progress(payload)

    assert percent == 67
    assert completed == 1
    assert errors == 1
