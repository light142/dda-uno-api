"""Admin simulation endpoints with SSE streaming."""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from fastapi.responses import StreamingResponse

from config import get_settings
from admin.auth import require_admin
from admin.simulation.schemas import SimulationCreate
from admin.simulation import service

router = APIRouter(prefix="/api/admin/simulations", tags=["Admin Simulations"])
settings = get_settings()


@router.post("")
async def start_simulation(
    body: SimulationCreate,
    background_tasks: BackgroundTasks,
    _=Depends(require_admin),
):
    """Start a new simulation run."""
    config = body.dict()
    sim = service.create_simulation(config)
    background_tasks.add_task(service.run_simulation, sim['id'], settings.MODEL_DIR)
    return {"id": sim['id'], "status": sim['status']}


@router.get("")
async def list_simulations(_=Depends(require_admin)):
    """List all simulations."""
    sims = service.list_simulations()
    return [
        {
            'id': s['id'],
            'mode': s['mode'],
            'status': s['status'],
            'config': s['config'],
            'games_total': s['games_total'],
            'games_done': s['games_done'],
            'final_win_rate': s.get('final_win_rate'),
            'created_at': s['created_at'],
        }
        for s in sims
    ]


@router.get("/{sim_id}")
async def get_simulation(sim_id: str, _=Depends(require_admin)):
    """Get simulation details including results."""
    sim = service.get_simulation(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return sim


@router.delete("/{sim_id}")
async def delete_simulation(sim_id: str, _=Depends(require_admin)):
    """Delete a simulation."""
    if not service.delete_simulation(sim_id):
        raise HTTPException(status_code=404, detail="Simulation not found")
    return {"ok": True}


@router.get("/{sim_id}/stream")
async def stream_simulation(sim_id: str, _=Depends(require_admin)):
    """SSE stream of simulation progress."""
    sim = service.get_simulation(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="Simulation not found")

    # If already completed, send final result immediately
    if sim['status'] == 'completed' and sim.get('result'):
        async def completed_stream():
            yield f"event: complete\ndata: {json.dumps(sim['result'])}\n\n"
        return StreamingResponse(completed_stream(), media_type="text/event-stream")

    if sim['status'] == 'failed':
        async def error_stream():
            yield f"event: error\ndata: {json.dumps({'message': sim.get('error_message', 'Unknown error')})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    # Subscribe to live events
    queue = service.subscribe(sim_id)

    async def event_stream():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    event_type = msg['event']
                    data = json.dumps(msg['data'])
                    yield f"event: {event_type}\ndata: {data}\n\n"
                    if event_type in ('complete', 'error'):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        finally:
            service.unsubscribe(sim_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
