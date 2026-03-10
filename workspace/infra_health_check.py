#!/usr/bin/env python3
"""
Infrastructure health check script for Koven Labs
Queries Docker containers and outputs a summary table
"""

import subprocess
import json
from datetime import datetime

def get_docker_containers():
    """Get all running Docker containers with their status"""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip().split('\n')
    except subprocess.CalledProcessError as e:
        print(f"Error querying Docker: {e}")
        return []

def parse_container_info(line):
    """Parse a single container line into a dictionary"""
    parts = line.split('\t')
    if len(parts) >= 4:
        return {
            "container_id": parts[0][:12],
            "name": parts[1],
            "status": parts[2],
            "ports": parts[3]
        }
    return None

def check_service_health(container_name):
    """Quick health check for known services"""
    known_services = {
        "llama-server": "GPU inference",
        "pleadly-api": "API service",
        "pleadly-postgres": "Database",
        "awaas-n8n": "Workflow automation",
        "awaas-postgres": "Workflow DB",
        "miko": "Miko assistant",
        "trading-postgres": "Trading DB",
        "caddy": "Reverse proxy",
        "grafana": "Monitoring",
        "master-conductor": "Conductor agent",
        "prometheus": "Metrics collector",
        "redis": "Cache layer"
    }
    return known_services.get(container_name, "Unknown service")

def main():
    print("\n" + "="*80)
    print(f"KOVEN LABS INFRASTRUCTURE HEALTH CHECK")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("="*80 + "\n")
    
    containers = get_docker_containers()
    
    if not containers:
        print("No running containers found.")
        return
    
    # Parse and display container info
    print(f"{'Container':<20} {'Service':<25} {'Status':<30} {'Ports':<20}")
    print("-"*95)
    
    running = 0
    unhealthy = 0
    
    for line in containers:
        if not line.strip():
            continue
            
        info = parse_container_info(line)
        if not info:
            continue
        
        service_type = check_service_health(info["name"])
        status = info["status"]
        
        # Count status types
        if "Up" in status:
            running += 1
        else:
            unhealthy += 1
        
        print(f"{info['container_id']:<20} {service_type:<25} {status:<30} {info['ports']:<20}")
    
    # Summary
    print("\n" + "-"*95)
    print(f"Total containers: {len(containers)}")
    print(f"Running: {running}")
    print(f"Unhealthy/Stopped: {unhealthy}")
    
    if unhealthy > 0:
        print("\n⚠️  WARNING: Some containers are not running!")
        for line in containers:
            if not line.strip():
                continue
            info = parse_container_info(line)
            if info and "Up" not in info["status"]:
                print(f"   - {info['name']} ({info['status']})")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    main()