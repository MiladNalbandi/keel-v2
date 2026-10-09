package keel.api.projects

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** v0.15.7 the folders keel may look in (KEEL_FOLDERS), the repos inside them, and a walk through their sub folders. */
@RestController
@RequestMapping("/api/folders")
class FolderController(private val folders: FolderService) {

    /** `{ roots: [{path, exists}], repos: [{path, name, root, project_id}], truncated }` */
    @GetMapping
    fun list(): Folders = folders.list()

    /** `{ path, root, parent, dirs: [{path, name, repo, project_id}], truncated }`; 403 outside the roots and the data folder. */
    @GetMapping("/browse")
    fun browse(@RequestParam(required = false) path: String?): Browse = folders.browse(path)
}
